# DevisBTP — Serveur Factur-X

Serveur Flask qui génère des factures PDF + XML Factur-X EN16931.

## Déploiement sur Render.com

1. Créez un compte sur https://render.com
2. Cliquez "New +" → "Web Service"
3. Choisissez "Deploy from existing code" → "Public Git repo"
   OU uploadez ce dossier via GitHub
4. Configurez :
   - **Name** : devisbtp-facturx
   - **Runtime** : Python 3
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `gunicorn app:app --bind 0.0.0.0:$PORT`
5. Cliquez "Create Web Service"
6. Attendez ~2 minutes → votre URL sera du type : https://devisbtp-facturx.onrender.com

## API

POST /generate  → ZIP (PDF + XML Factur-X)
POST /pdf       → PDF seul
POST /xml       → XML seul
GET  /          → health check
