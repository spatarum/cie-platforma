# Platforma experți CIE (MVP)

Platformă web pentru administratori și experți ai Comisiei parlamentare pentru integrare europeană.

Funcționalități principale:
- autentificare (Administrator / Expert)
- chestionare cu termen limită
- întrebări deschise (max. 1500 caractere/răspuns)
- max. 20 întrebări per chestionar
- alocare chestionare pe **capitole** și **criterii**
- alocare competențe experților pe **capitole** și **criterii**
- expert: salvează/editează până la termen; după termen – doar vizualizare
- export răspunsuri: CSV / Excel (XLSX) / PDF

> Toată interfața este în limba română. Font: **Onest**.

---

## Pornire locală (Windows / macOS / Linux)

1) Instalează Python 3.11+.

2) În folderul proiectului:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
python manage.py migrate
python manage.py seed_referinte
python manage.py createsuperuser
python manage.py runserver
```

3) Deschide în browser:
- Login: `http://127.0.0.1:8000/login/`
- Panou admin (custom): `http://127.0.0.1:8000/administrare/`
- Admin avansat Django: `http://127.0.0.1:8000/django-admin/`

---

## Deploy rapid pe Render (gratis pentru test)

1) Creează un repo pe GitHub și urcă toate fișierele proiectului.

2) În Render:
- **New → Blueprint**
- alege repo-ul
- Render va folosi `render.yaml` și va crea automat Web Service + PostgreSQL.

3) **Crearea contului de Administrator (fără Shell, pe planul gratuit)**

Pe planul gratuit Render, accesul la Shell/SSH nu este disponibil. În schimb, proiectul include o comandă automată
(`ensure_superuser`) care creează un administrator la deploy, dacă nu există deja.

În Render → Web Service → **Environment**, adaugă variabilele:
- `CIE_ADMIN_EMAIL` = emailul tău
- `CIE_ADMIN_PASSWORD` = o parolă temporară (o vei putea schimba ulterior)
- (opțional) `CIE_ADMIN_USERNAME` = username (dacă nu pui, se folosește emailul)

Apoi declanșează un redeploy (Deploy latest commit).

După redeploy, te poți loga la `.../login/` cu credențialele de mai sus.

---

## Cum lucrezi ca Administrator

1) Adaugi experți: **Administrare → Experți → Expert nou**
2) Creezi chestionar: **Administrare → Chestionare → Chestionar nou**
   - setezi termen limită
   - bifezi capitole/criterii
   - completezi 1–20 întrebări
3) Export: **Administrare → Export** (selectezi chestionare/capitole/criterii + format).

---

## Cum lucrează Expertul

1) Se loghează
2) Vede chestionarele alocate domeniilor sale (capitole/criterii)
3) Completează, salvează, revine, editează până la termen
4) După termen: doar vizualizare

---

## Note
- Pentru schimbări în capitole/clustere/criterii: **Administrare → Capitole & criterii → Administrare avansată**.
- În producție, setează `DJANGO_SECURE_SSL_REDIRECT=true` și folosește HTTPS.

