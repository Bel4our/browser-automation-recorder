const express = require('express');
const multer = require('multer');
const { Pool } = require('pg');
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = 3000; 

const UPLOADS_DIR = path.resolve(__dirname, 'uploads');
if (!fs.existsSync(UPLOADS_DIR)) {
    fs.mkdirSync(UPLOADS_DIR, { recursive: true });
}

const pool = new Pool({
    host: process.env.DB_HOST || 'db',
    port: parseInt(process.env.DB_PORT || '5432', 10),
    database: process.env.DB_NAME || 'videos',
    user: process.env.DB_USER || 'video_user',
    password: process.env.DB_PASSWORD || 'video_pass',
});

async function initDb(retries = 30, delayMs = 2000) {
    for (let i = 0; i < retries; i++) {
        try {
            const client = await pool.connect();
            console.log('Connected to PostgreSQL successfully');

            await client.query(`
                CREATE TABLE IF NOT EXISTS videos (
                    id SERIAL PRIMARY KEY,
                    company_name TEXT NOT NULL UNIQUE,
                    file_path TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            `);
            client.release();
            return;
        } catch (err) {
            console.log(`Waiting for database to be ready... (${i + 1}/${retries})`);
            await new Promise((res) => setTimeout(res, delayMs));
        }
    }
    console.error('Could not connect to PostgreSQL. Exiting.');
    process.exit(1);
}

initDb();

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

app.post('/upload', upload.single('file'), async (req, res) => {
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
        VALUES ($1, $2, $3, NOW(), NOW())
        ON CONFLICT (company_name) DO UPDATE SET
            file_path = EXCLUDED.file_path,
            video_url = EXCLUDED.video_url,
            updated_at = NOW();
    `;

    try {
        await pool.query(upsertSql, [companyName, absoluteFilePath, videoUrl]);
        return res.status(200).json({
            company_name: companyName,
            video_url: videoUrl
        });
    } catch (err) {
        console.error('DB Insert Error:', err.message);
        return res.status(500).json({ error: 'Database write error' });
    }
});

app.get('/get_video_url', async (req, res) => {
    const companyName = req.query.company_name ? req.query.company_name.trim() : null;

    if (!companyName) {
        return res.status(400).json({ error: 'company_name query parameter is required' });
    }

    try {
        const result = await pool.query(
            'SELECT company_name, video_url FROM videos WHERE company_name = $1;',
            [companyName]
        );
        if (result.rows.length === 0) {
            return res.status(404).json({ error: `Company '${companyName}' not found` });
        }
        return res.status(200).json(result.rows[0]);
    } catch (err) {
        return res.status(500).json({ error: 'Database query error' });
    }
});

app.get('/media/:filename', (req, res) => {
    const filePath = path.join(UPLOADS_DIR, req.params.filename);
    if (!fs.existsSync(filePath)) {
        return res.status(404).send('File not found');
    }
    res.setHeader('Content-Type', 'video/webm');
    res.sendFile(filePath);
});

app.get('/videos', async (req, res) => {
    try {
        const result = await pool.query('SELECT * FROM videos ORDER BY id ASC;');
        res.json(result.rows);
    } catch (err) {
        res.status(500).json({ error: 'Database error' });
    }
});

app.listen(PORT, '0.0.0.0', () => {
    console.log(`Web Service running on port ${PORT}`);
});