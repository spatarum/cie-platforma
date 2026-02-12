# Platforma experți CIE (MVP) – Chestionare + Newsletter

Platformă web (Django) pentru administratori și experți ai Comisiei parlamentare pentru integrare europeană.

- Interfață în limba română
- Font UI: **Onest**
- Deploy curent: **GitHub + Render**

---

## Funcționalități

### Roluri și acces
- **Administrator** (utilizator `is_staff=True`): acces la panoul custom `/administrare/` + (opțional) Django Admin `/django-admin/`
- **Expert** (utilizator `is_staff=False`): acces la zona `/expert/`

### Chestionare
- creare / editare chestionare cu **termen limită**
- chestionare în două moduri:
  - **General** (vizibile pentru toți experții)
  - **alocate pe domenii**: **Capitole** și/sau **Foi de parcurs (Criterii)**
- fiecare chestionar poate avea **1–20 întrebări**
- întrebări deschise (text): **max. 3000 caractere / răspuns**
- notificări pe email (la creare):
  - General → email către toți experții
  - pe Capitole/Criterii → email către experții care au acele domenii în profil

### Răspunsuri (Expert)
- răspunsurile se salvează ca **Ciornă** sau se marchează **Trimis**
- chiar dacă au fost marcate „Trimis”, răspunsurile pot fi editate până la termen
- după termen: **doar vizualizare**

### Newsletter
- Administrator: creează / editează / trimite newsletter către **toți experții activi**
- Expert: listă newslettere + pagină de detaliu („vezi online”)
- conținutul suportă linkuri în format: `[text](https://exemplu.md)`

### Import (CSV)
- **Import experți (CSV)**
  - cheie unică: `email`
  - rând existent → se actualizează
  - expert nou → se creează și se generează parolă temporară
  - după import: se generează un **raport** și (dacă e cazul) un fișier cu **credentiale**
- **Import chestionare (CSV)**
  - poate crea sau actualiza (dacă `id` există)
  - întrebări: `intrebare_1 ... intrebare_20`
  - pentru chestionarele noi se trimit notificări pe email către experții relevanți

### Export (Admin)
- export răspunsuri: **CSV / Excel (XLSX) / PDF**
- selecție prin:
  - chestionare anume
  - filtre pe Capitole / Foi de parcurs (Criterii)
  - opțiune pentru includerea chestionarelor **General**

### Arhivare (ștergere logică)
- arhivare/restabilire **experți** și **chestionare** (nu se șterg definitiv)

---

## Cerințe
- Python **3.11+**
- (opțional, recomandat în producție) PostgreSQL

Dependențe principale: Django 4.2+, Gunicorn, WhiteNoise, openpyxl, reportlab.

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

## Configurare (variabile de mediu)

Aplicația citește variabilele de mediu (poți porni de la `.env.example`).

### Esențiale
- `SECRET_KEY` – obligatoriu în producție
- `DJANGO_DEBUG` – `false` în producție
- `DATABASE_URL` – pentru PostgreSQL (Render îl furnizează automat)

### Domeniu și securitate
- `DJANGO_ALLOWED_HOSTS` – listă separată prin virgule (ex: `experti.parlament.md,cie-platforma-experti.onrender.com`)
- `DJANGO_CSRF_TRUSTED_ORIGINS` – listă separată prin virgule (ex: `https://experti.parlament.md`)
- `SITE_URL` – URL-ul public al platformei (folosit în linkuri din email): `https://experti.parlament.md`
- `DJANGO_SECURE_SSL_REDIRECT` – `true` pentru a forța HTTPS

### Creare automată Administrator (util pentru Render fără SSH)
La deploy, rulează `python manage.py ensure_superuser` (o singură dată, dacă nu există deja superuser).

- `CIE_ADMIN_EMAIL`
- `CIE_ADMIN_PASSWORD`
- (opțional) `CIE_ADMIN_USERNAME`
- (opțional) `CIE_ADMIN_FIRST_NAME`, `CIE_ADMIN_LAST_NAME`

---

## Configurare trimitere emailuri (IMPORTANT)

Platforma trimite emailuri pentru:
- notificare „chestionar nou”
- trimitere newsletter

Implicit, în lipsa configurației, proiectul folosește `console.EmailBackend` – emailurile apar în loguri, **nu se trimit**.

Pentru trimitere reală prin SMTP setează:

- `DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`
- `EMAIL_HOST` (ex: `smtp.parlament.md` sau provider extern)
- `EMAIL_PORT` (uzual 587 pentru TLS)
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS=true` (recomandat)
- `EMAIL_USE_SSL=false` (dacă folosești 587)
- `DEFAULT_FROM_EMAIL` (ex: `no-reply@parlament.md`)

Recomandări:
- setează `SITE_URL` corect (altfel linkurile „Vezi online” pot fi greșite)
- folosește un domeniu cu SPF/DKIM/DMARC configurat, ca să nu ajungă în Spam

---

## Deploy pe Render (Blueprint)

Repo-ul include `render.yaml` + `build.sh`.

1) Urcă proiectul pe GitHub.
2) În Render: **New → Blueprint** → selectează repo-ul.
3) Render va crea automat:
   - Web Service (Gunicorn)
   - PostgreSQL (db)

`build.sh` rulează automat:
- `migrate`
- `seed_referinte`
- `ensure_superuser`
- `collectstatic`

### Variabile recomandate în Render → Web Service → Environment
- `CIE_ADMIN_EMAIL`, `CIE_ADMIN_PASSWORD` (pentru admin inițial)
- `SITE_URL` (ex: `https://experti.parlament.md`)
- `DJANGO_ALLOWED_HOSTS` (include domeniul)
- `DJANGO_CSRF_TRUSTED_ORIGINS` (include `https://...`)
- setările SMTP (secțiunea de email)
- `DJANGO_SECURE_SSL_REDIRECT=true` (după ce certificatul HTTPS este activ)

---

## Domeniu custom: experti.parlament.md (Render)

Dacă aplicația rămâne găzduită pe Render și doar mapăm domeniul:

1) În Render → Web Service → **Settings → Custom Domains**: adaugă `experti.parlament.md`.
2) În DNS pentru `parlament.md`: adaugă înregistrarea cerută de Render (de regulă **CNAME** pentru subdomeniu) către hostname-ul serviciului Render.
3) În Render → Environment, setează:
   - `DJANGO_ALLOWED_HOSTS=experti.parlament.md`
   - `DJANGO_CSRF_TRUSTED_ORIGINS=https://experti.parlament.md`
   - `SITE_URL=https://experti.parlament.md`
4) După activarea HTTPS, setează `DJANGO_SECURE_SSL_REDIRECT=true`.

---

## Note
- Pentru editări în Capitole/Clustere/Foi de parcurs: `/administrare/referinte/` sau `/django-admin/`
- În producție recomandat: HTTPS + `DJANGO_SECURE_SSL_REDIRECT=true`
