# Messen

Mess-Steuerung: Prüfplan-gesteuerte Messungsplanung für Werker.

## Deployment auf Vercel

1. Repo mit GitHub verbinden auf [vercel.com](https://vercel.com)
2. "New Project" → GitHub-Repo "Messen" auswählen
3. Framework Preset: **Other**
4. Deploy klicken — fertig!

## Lokal starten

```bash
pip install -r requirements.txt
cd api && flask --app index run
```

## Projektstruktur

```
api/index.py       – Flask API (Serverless Function)
public/index.html  – Frontend
vercel.json        – Vercel Routing
requirements.txt   – Python-Abhängigkeiten
```
