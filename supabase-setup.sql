-- ============================================
-- Supabase Setup: Artikeldatenbank
-- Dieses SQL im Supabase SQL Editor ausführen
-- (Dashboard → SQL Editor → New Query)
-- ============================================

-- 1. Tabelle erstellen
CREATE TABLE IF NOT EXISTS artikel (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    artikel_nr TEXT NOT NULL,
    bezeichnung TEXT NOT NULL DEFAULT '',
    ident_nr TEXT NOT NULL DEFAULT '',
    zeichnungs_nr TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    messplan_file TEXT,
    zeichnung_file TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(artikel_nr, version)
);

-- 2. Row Level Security aktivieren und öffentlichen Zugriff erlauben
ALTER TABLE artikel ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow public read" ON artikel FOR SELECT USING (true);
CREATE POLICY "Allow public insert" ON artikel FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update" ON artikel FOR UPDATE USING (true);
CREATE POLICY "Allow public delete" ON artikel FOR DELETE USING (true);

-- 3. Storage Bucket für PDFs erstellen
INSERT INTO storage.buckets (id, name, public)
VALUES ('artikel-dateien', 'artikel-dateien', true)
ON CONFLICT DO NOTHING;

-- 4. Storage Policies für öffentlichen Upload/Download
CREATE POLICY "Allow public upload" ON storage.objects
    FOR INSERT WITH CHECK (bucket_id = 'artikel-dateien');

CREATE POLICY "Allow public read" ON storage.objects
    FOR SELECT USING (bucket_id = 'artikel-dateien');

CREATE POLICY "Allow public update" ON storage.objects
    FOR UPDATE USING (bucket_id = 'artikel-dateien');

CREATE POLICY "Allow public delete" ON storage.objects
    FOR DELETE USING (bucket_id = 'artikel-dateien');
