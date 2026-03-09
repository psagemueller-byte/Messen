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
    taktzeit INTEGER NOT NULL DEFAULT 30,
    messplan_file TEXT,
    zeichnung_file TEXT,
    gesperrt BOOLEAN NOT NULL DEFAULT false,
    marker_positionen JSONB DEFAULT '{}',
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

-- 5. Messhistorie-Tabelle für Audit-Log
CREATE TABLE IF NOT EXISTS messhistorie (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    artikel_id BIGINT REFERENCES artikel(id) ON DELETE CASCADE,
    pos_nr TEXT NOT NULL,
    messwert DOUBLE PRECISION,
    teil_nr INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE messhistorie ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow public read" ON messhistorie FOR SELECT USING (true);
CREATE POLICY "Allow public insert" ON messhistorie FOR INSERT WITH CHECK (true);

-- 6. Fertigungsauftrag-Tabelle
CREATE TABLE IF NOT EXISTS fertigungsauftrag (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    artikel_id BIGINT REFERENCES artikel(id) ON DELETE CASCADE,
    auftragsnummer TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'aktiv',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

ALTER TABLE fertigungsauftrag ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow public read" ON fertigungsauftrag FOR SELECT USING (true);
CREATE POLICY "Allow public insert" ON fertigungsauftrag FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update" ON fertigungsauftrag FOR UPDATE USING (true);
CREATE POLICY "Allow public delete" ON fertigungsauftrag FOR DELETE USING (true);

-- 7. Auftrag-Messwerte-Tabelle (Dokumentation jedes Messwerts pro Auftrag)
CREATE TABLE IF NOT EXISTS auftrag_messwerte (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    auftrag_id BIGINT REFERENCES fertigungsauftrag(id) ON DELETE CASCADE,
    pos_nr TEXT NOT NULL,
    messwert DOUBLE PRECISION,
    teil_nr INTEGER,
    in_toleranz BOOLEAN DEFAULT true,
    freigegeben BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE auftrag_messwerte ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow public read" ON auftrag_messwerte FOR SELECT USING (true);
CREATE POLICY "Allow public insert" ON auftrag_messwerte FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update" ON auftrag_messwerte FOR UPDATE USING (true);
CREATE POLICY "Allow public delete" ON auftrag_messwerte FOR DELETE USING (true);

-- 8. Wareneingang Prüfpunkte (visuelle Prüfanweisungen pro Artikel)
CREATE TABLE IF NOT EXISTS we_pruefpunkte (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    artikel_id BIGINT REFERENCES artikel(id) ON DELETE CASCADE,
    nummer TEXT NOT NULL,
    form TEXT NOT NULL DEFAULT 'circle',  -- 'circle' oder 'rect'
    x DOUBLE PRECISION NOT NULL,
    y DOUBLE PRECISION NOT NULL,
    titel TEXT NOT NULL DEFAULT '',
    anweisung TEXT NOT NULL DEFAULT '',
    fotos JSONB DEFAULT '[]',  -- Array von Base64-Strings
    pruef_prozent DOUBLE PRECISION NOT NULL DEFAULT 100,  -- Stichproben-Prozent (0-100)
    mindest_prueflos INTEGER NOT NULL DEFAULT 1,           -- Mindestanzahl zu prüfen
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE we_pruefpunkte ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow public read" ON we_pruefpunkte FOR SELECT USING (true);
CREATE POLICY "Allow public insert" ON we_pruefpunkte FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update" ON we_pruefpunkte FOR UPDATE USING (true);
CREATE POLICY "Allow public delete" ON we_pruefpunkte FOR DELETE USING (true);

-- 9. WE Prüf-Presets (wiederverwendbare Vorlagen für Prüfmerkmale)
CREATE TABLE IF NOT EXISTS we_presets (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    beschreibung TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE we_presets ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow public read" ON we_presets FOR SELECT USING (true);
CREATE POLICY "Allow public insert" ON we_presets FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update" ON we_presets FOR UPDATE USING (true);
CREATE POLICY "Allow public delete" ON we_presets FOR DELETE USING (true);

-- 10. WE Preset-Merkmale (Prüfmerkmale innerhalb eines Presets, ohne x/y)
CREATE TABLE IF NOT EXISTS we_preset_merkmale (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    preset_id BIGINT REFERENCES we_presets(id) ON DELETE CASCADE,
    nummer TEXT NOT NULL,
    form TEXT NOT NULL DEFAULT 'circle',
    titel TEXT NOT NULL DEFAULT '',
    anweisung TEXT NOT NULL DEFAULT '',
    fotos JSONB DEFAULT '[]',
    pruef_prozent DOUBLE PRECISION NOT NULL DEFAULT 100,
    mindest_prueflos INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE we_preset_merkmale ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow public read" ON we_preset_merkmale FOR SELECT USING (true);
CREATE POLICY "Allow public insert" ON we_preset_merkmale FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update" ON we_preset_merkmale FOR UPDATE USING (true);
CREATE POLICY "Allow public delete" ON we_preset_merkmale FOR DELETE USING (true);
