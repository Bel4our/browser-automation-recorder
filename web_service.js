const express = require('express');
const multer = require('multer');
const { Pool } = require('pg');
const { execFile } = require('child_process');
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = 3000;
const PYTHON_BIN = process.env.PYTHON_BIN || 'python3';
const FFMPEG_BIN = process.env.FFMPEG_BIN || 'ffmpeg';
const APPLY_ZOOM_SCRIPT = path.resolve(__dirname, 'apply_zoom.py');

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
                    processed_video_url TEXT,
                    zoom_events JSONB,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            `);
            await client.query(`ALTER TABLE videos ADD COLUMN IF NOT EXISTS processed_video_url TEXT;`);
            await client.query(`ALTER TABLE videos ADD COLUMN IF NOT EXISTS zoom_events JSONB;`);
            await client.query(`ALTER TABLE videos ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';`);
            await client.query(`ALTER TABLE videos ADD COLUMN IF NOT EXISTS error_message TEXT;`);
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

const upload = multer({ dest: UPLOADS_DIR });
app.use(express.json());

function sanitizeCompany(raw) {
    const cleaned = (raw || 'video').replace(/[^a-zA-Z0-9_-]/g, '');
    return cleaned || 'video';
}

function runApplyZoom(inputPath, sidecarPath, outputPath) {
    return new Promise((resolve, reject) => {
        const args = [
            APPLY_ZOOM_SCRIPT,
            '--video', inputPath,
            '--sidecar', sidecarPath,
            '--output', outputPath,
            '--ffmpeg', FFMPEG_BIN,
        ];
        execFile(PYTHON_BIN, args, { maxBuffer: 10 * 1024 * 1024 }, (err, stdout, stderr) => {
            if (err) {
                return reject(new Error(stderr || err.message));
            }
            resolve(stdout.trim());
        });
    });
}

async function processVideoJob(videoId, baseUrl) {
    const res = await pool.query('SELECT * FROM videos WHERE id = $1;', [videoId]);
    if (res.rows.length === 0) {
        throw new Error('Video record not found');
    }
    const record = res.rows[0];
    const inputPath = record.file_path;
    const sidecarPath = `${inputPath}.zoom.json`;

    if (!fs.existsSync(sidecarPath) && record.zoom_events) {
        fs.writeFileSync(sidecarPath, JSON.stringify(record.zoom_events, null, 2), 'utf-8');
    }

    if (!fs.existsSync(sidecarPath)) {
        await pool.query(
            `UPDATE videos SET status = 'skipped', error_message = NULL, updated_at = NOW() WHERE id = $1;`,
            [videoId]
        );
        return {
            id: record.id,
            company_name: record.company_name,
            status: 'skipped',
            video_url: record.video_url,
            processed_video_url: record.processed_video_url,
        };
    }

    await pool.query(
        `UPDATE videos SET status = 'processing', error_message = NULL, updated_at = NOW() WHERE id = $1;`,
        [videoId]
    );

    const parsed = path.parse(inputPath);
    const outputFilename = `${parsed.name}_zoomed${parsed.ext}`;
    const outputPath = path.join(parsed.dir, outputFilename);

    try {
        await runApplyZoom(inputPath, sidecarPath, outputPath);
        const origin = baseUrl || new URL(record.video_url).origin;
        const processedUrl = `${origin}/media/${outputFilename}`;

        await pool.query(
            `UPDATE videos SET processed_video_url = $1, status = 'done', error_message = NULL, updated_at = NOW() WHERE id = $2;`,
            [processedUrl, videoId]
        );

        return {
            id: record.id,
            company_name: record.company_name,
            status: 'done',
            video_url: record.video_url,
            processed_video_url: processedUrl,
        };
    } catch (err) {
        await pool.query(
            `UPDATE videos SET status = 'failed', error_message = $1, updated_at = NOW() WHERE id = $2;`,
            [err.message, videoId]
        );
        throw err;
    }
}

app.post(
    '/upload',
    upload.fields([
        { name: 'file', maxCount: 1 },
        { name: 'sidecar', maxCount: 1 },
    ]),
    async (req, res) => {
        const companyName = req.body.company_name ? req.body.company_name.trim() : null;
        const videoFile = req.files && req.files.file ? req.files.file[0] : null;
        const sidecarFile = req.files && req.files.sidecar ? req.files.sidecar[0] : null;

        if (!videoFile || !companyName) {
            if (videoFile && fs.existsSync(videoFile.path)) fs.unlinkSync(videoFile.path);
            if (sidecarFile && fs.existsSync(sidecarFile.path)) fs.unlinkSync(sidecarFile.path);
            return res.status(400).json({ error: 'Both file and company_name are required' });
        }

        const sanitized = sanitizeCompany(companyName);
        const finalFilename = `${sanitized}.webm`;
        const finalFilePath = path.join(UPLOADS_DIR, finalFilename);
        const sidecarPath = `${finalFilePath}.zoom.json`;

        fs.renameSync(videoFile.path, finalFilePath);

        let zoomEventsJson = null;
        let hasSidecar = false;

        if (sidecarFile) {
            fs.renameSync(sidecarFile.path, sidecarPath);
            hasSidecar = true;
        } else if (req.body.zoom_data) {
            fs.writeFileSync(sidecarPath, req.body.zoom_data, 'utf-8');
            hasSidecar = true;
        } else if (fs.existsSync(sidecarPath)) {
            fs.unlinkSync(sidecarPath);
        }

        if (hasSidecar) {
            try {
                zoomEventsJson = JSON.parse(fs.readFileSync(sidecarPath, 'utf-8'));
            } catch (e) {
                zoomEventsJson = null;
            }
        }

        const baseUrl = `${req.protocol}://${req.get('host')}`;
        const videoUrl = `${baseUrl}/media/${finalFilename}`;
        const initialStatus = hasSidecar ? 'pending' : 'skipped';

        const upsertSql = `
            INSERT INTO videos (company_name, file_path, video_url, processed_video_url, zoom_events, status, error_message, created_at, updated_at)
            VALUES ($1, $2, $3, NULL, $4, $5, NULL, NOW(), NOW())
            ON CONFLICT (company_name) DO UPDATE SET
                file_path = EXCLUDED.file_path,
                video_url = EXCLUDED.video_url,
                processed_video_url = NULL,
                zoom_events = EXCLUDED.zoom_events,
                status = EXCLUDED.status,
                error_message = NULL,
                updated_at = NOW()
            RETURNING id;
        `;

        try {
            const dbRes = await pool.query(upsertSql, [
                companyName,
                finalFilePath,
                videoUrl,
                zoomEventsJson,
                initialStatus,
            ]);
            const videoId = dbRes.rows[0].id;

            if (hasSidecar) {
                setImmediate(() => {
                    processVideoJob(videoId, baseUrl).catch((err) => {
                        console.error(`Background zoom failed for id=${videoId}:`, err.message);
                    });
                });
            }

            return res.status(200).json({
                id: videoId,
                company_name: companyName,
                video_url: videoUrl,
                processed_video_url: null,
                status: initialStatus,
            });
        } catch (err) {
            console.error('DB Insert Error:', err.message);
            return res.status(500).json({ error: 'Database write error' });
        }
    }
);

app.post('/jobs/:id/process', async (req, res) => {
    const videoId = parseInt(req.params.id, 10);
    if (Number.isNaN(videoId)) {
        return res.status(400).json({ error: 'Invalid job id' });
    }

    try {
        if (req.body && req.body.events) {
            const found = await pool.query('SELECT file_path FROM videos WHERE id = $1;', [videoId]);
            if (found.rows.length === 0) {
                return res.status(404).json({ error: 'Video not found' });
            }
            const sidecarPath = `${found.rows[0].file_path}.zoom.json`;
            fs.writeFileSync(sidecarPath, JSON.stringify(req.body, null, 2), 'utf-8');
            await pool.query('UPDATE videos SET zoom_events = $1, updated_at = NOW() WHERE id = $2;', [
                req.body,
                videoId,
            ]);
        }

        const baseUrl = `${req.protocol}://${req.get('host')}`;
        const result = await processVideoJob(videoId, baseUrl);
        return res.status(200).json(result);
    } catch (err) {
        if (err.message === 'Video record not found') {
            return res.status(404).json({ error: err.message });
        }
        return res.status(500).json({ error: err.message });
    }
});

app.get('/jobs/:id', async (req, res) => {
    const videoId = parseInt(req.params.id, 10);
    if (Number.isNaN(videoId)) {
        return res.status(400).json({ error: 'Invalid job id' });
    }

    try {
        const result = await pool.query(
            'SELECT id, company_name, video_url, processed_video_url, status, error_message, created_at, updated_at FROM videos WHERE id = $1;',
            [videoId]
        );
        if (result.rows.length === 0) {
            return res.status(404).json({ error: 'Job not found' });
        }
        return res.status(200).json(result.rows[0]);
    } catch (err) {
        return res.status(500).json({ error: 'Database query error' });
    }
});

app.get('/get_video_url', async (req, res) => {
    const companyName = req.query.company_name ? req.query.company_name.trim() : null;

    if (!companyName) {
        return res.status(400).json({ error: 'company_name query parameter is required' });
    }

    try {
        const result = await pool.query(
            'SELECT id, company_name, video_url, processed_video_url, status FROM videos WHERE company_name = $1;',
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
    if (req.params.filename.endsWith('.json')) {
        res.setHeader('Content-Type', 'application/json');
    } else {
        res.setHeader('Content-Type', 'video/webm');
    }
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