# Simmer

An open-source, multilingual recipe manager. Organize, discover, and share your favorite dishes without the clutter.

**Live demo:** [https://recipes.meide-it.org](https://recipes.meide-it.org)


## Features

- **Full CRUD** — create, edit, delete, and browse recipes
- **Drafts** — save recipes as drafts, visible only to you
- **Multi-language recipes** — link translations of the same dish (e.g. "Chocolate Cake" / "Schokoladenkuchen")
- **Multi-language UI** — English, German, and Russian interface
- **Multi-language ingredients** — name ingredients in multiple languages with fallback chains
- **Step-by-step instructions** — each step has its own set of ingredients
- **Portion scaling** — dynamically scale ingredient quantities up/down
- **Smart unit conversion** — convert between grams, cups, mL, etc. per ingredient (density-aware)
- **Bound units** — bind units to specific ingredients with per-ingredient gram overrides
- **Ingredient & unit management** — create custom ingredients and units with density-aware conversion
- **Tags & filtering** — tag recipes and filter by tags on the home page
- **Shopping cart** — add recipes with custom portions, view aggregated ingredients, copy to clipboard
- **Work & cooking time** — track preparation and cooking time per recipe
- **Full-text search** — search recipes by title, filter by tags
- **Download as Markdown** — export any recipe as a `.md` file
- **Interactive onboarding** — first-visit guided tour with live demos
- **User accounts** — register, log in, change settings (username, password)
- **Admin panel** — manage users (promote/demote, pause, delete), add demo data
- **Rate-limited & secure** — CSRF protection, bcrypt passwords, security headers

## Tech Stack

Python 3 · Flask · SQLAlchemy · SQLite/PostgreSQL · Bootstrap 5 · Gunicorn

## Quick Start

```bash
git clone https://github.com/bztt282tbz-sys/recipes
cd recipes
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

The app creates the database and seeds default data on first run. Open `http://localhost:5000`.

### Admin access

The first user to register automatically becomes an admin. No default demo account exists.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | auto-generated | Flask secret key |
| `FLASK_ENV` | `production` | Set to anything else for debug mode |
| `DATABASE_URL` | `sqlite:///site.db` | Database URI |
| `REDIS_URL` | `memory://` | Redis for rate-limit storage |

## License

MIT
