const express = require('express');
const multer = require('multer');
const sqlite3 = require('sqlite3').verbose();
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = process.env.FLASK_PORT || process.env.PORT || 8000;
const HOST = process.env.FLASK_HOST || '0.0.0.0';

const UPLOADS_DIR = path.resolve(__dirname, 'uploads');
const DB_PATH = path.resolve(__dirname, 'videos.db');

if (!fs.existsSync(UPLOADS_DIR)) {
    fs.mkdirSync(UPLOADS_DIR, { recursive: true });
}

const db = new sqlite3.Database(DB_PATH, (err) => {
    if (err) {
        console.error('Failed to connect to SQLite:', err.message);
        process.exit(1);
    }
});

db.serialize(() => {
    db.run('PRAGMA journal_mode = WAL;');
    db.run(`
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL UNIQUE,
            file_path TEXT NOT NULL,
            video_url TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    `);
});

const storage = multer.diskStorage({
    destination: (req, file, cb) => {
        cb(null, UPLOADS_DIR);
    },
    filename: (req, file, cb) => {
        const rawCompany = req.body.company_name || 'video';
        const sanitized = rawCompany.replace(/[^a-zA-Z0-9_-]/g, '');
        cb(null, `${sanitized}.webm`);
    }
});
const upload = multer({ storage });

app.use(express.json());

app.post('/upload', upload.single('file'), (req, res) => {
    const companyName = req.body.company_name ? req.body.company_name.trim() : null;

    if (!req.file || !companyName) {
        return res.status(400).json({ error: 'Both file and company_name are required' });
    }

    const host = req.get('host');
    const protocol = req.protocol;
    const videoUrl = `${protocol}://${host}/media/${req.file.filename}`;
    const absoluteFilePath = path.resolve(req.file.path);

    const upsertSql = `
        INSERT INTO videos (company_name, file_path, video_url, created_at, updated_at)
        VALUES (?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(company_name) DO UPDATE SET
            file_path = excluded.file_path,
            video_url = excluded.video_url,
            updated_at = datetime('now')
    `;

    db.run(upsertSql, [companyName, absoluteFilePath, videoUrl], function (err) {
        if (err) {
            console.error('DB Insert Error:', err.message);
            return res.status(500).json({ error: 'Database write error' });
        }

        return res.status(200).json({
            company_name: companyName,
            video_url: videoUrl
        });
    });
});

app.get('/get_video_url', (req, res) => {
    const companyName = req.query.company_name ? req.query.company_name.trim() : null;

    if (!companyName) {
        return res.status(400).json({ error: 'company_name query parameter is required' });
    }

    db.get('SELECT company_name, video_url FROM videos WHERE company_name = ?', [companyName], (err, row) => {
        if (err) {
            return res.status(500).json({ error: 'Database query error' });
        }
        if (!row) {
            return res.status(404).json({ error: `Company '${companyName}' not found` });
        }
        return res.status(200).json({
            company_name: row.company_name,
            video_url: row.video_url
        });
    });
});

app.get('/media/:filename', (req, res) => {
    const filePath = path.join(UPLOADS_DIR, req.params.filename);
    if (!fs.existsSync(filePath)) {
        return res.status(404).send('File not found');
    }
    res.setHeader('Content-Type', 'video/webm');
    res.sendFile(filePath);
});

app.get('/videos', (req, res) => {
    db.all('SELECT * FROM videos', [], (err, rows) => {
        if (err) return res.status(500).json({ error: 'Database error' });
        res.json(rows);
    });
});

app.listen(PORT, HOST, () => {
    console.log(`Web Service running on http://${HOST}:${PORT}`);
});