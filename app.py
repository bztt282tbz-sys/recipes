from flask import Flask, render_template, redirect, url_for, request, flash, abort, session
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime, date
import os
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or os.urandom(24).hex()
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['WTF_CSRF_ENABLED'] = True
app.config['WTF_CSRF_TIME_LIMIT'] = 3600

app.config['JSON_SORT_KEYS'] = False
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False

@app.route("/lang/<lang_code>")
def set_language(lang_code):
    if lang_code in ('en', 'de', 'ru'):
        session['lang'] = lang_code
    back_to = request.args.get('back_to')
    if back_to:
        return redirect(back_to)
    return redirect(request.headers.get('Referer', url_for('home')))

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.environ.get('REDIS_URL') or "memory://"
)

csrf = CSRFProtect(app)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.session_protection = 'strong'
bcrypt = Bcrypt(app)

# --- Utilities ---

def convert_to_grams(amount, unit_obj, ingredient):
    if not unit_obj:
        return amount
    if unit_obj.unit_type == 'mass':
        return amount * unit_obj.grams_conversion
    elif unit_obj.unit_type == 'volume':
        density = ingredient.density if ingredient.density else 1.0
        return amount * unit_obj.grams_conversion * density
    return amount

# --- Models ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    recipes = db.relationship('Recipe', backref='author', lazy=True)
    ingredients = db.relationship('Ingredient', backref='author', lazy=True)
    units = db.relationship('Unit', backref='author', lazy=True)

class Unit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), nullable=False)
    unit_type = db.Column(db.String(10), nullable=False)
    grams_conversion = db.Column(db.Float, nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_bound = db.Column(db.Boolean, default=False)
    ingredient_units = db.relationship('UnitIngredient', backref='unit', cascade="all, delete-orphan", lazy=True)

    def get_applicable_units(self, ingredient_id):
        if not self.is_bound:
            return True
        for ui in self.ingredient_units:
            if ui.ingredient_id == ingredient_id:
                return True
        return False

class Ingredient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    density = db.Column(db.Float, nullable=True)
    density_unit = db.Column(db.String(10), default='g/ml')
    grams_per_unit = db.Column(db.Float, nullable=True)
    unit_name = db.Column(db.String(30), nullable=True)
    preferred_unit_id = db.Column(db.Integer, db.ForeignKey('unit.id'), nullable=True)
    preferred_unit = db.relationship('Unit', foreign_keys=[preferred_unit_id])
    comment = db.Column(db.String(100), nullable=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    unit_associations = db.relationship('UnitIngredient', backref='ingredient', cascade="all, delete-orphan", lazy=True)

    def get_available_units(self):
        units = Unit.query.all()
        available = []
        for unit in units:
            if unit.is_bound:
                for ui in unit.ingredient_units:
                    if ui.ingredient_id == self.id:
                        available.append({
                            'id': unit.id,
                            'name': unit.name,
                            'unit_type': unit.unit_type,
                            'grams_conversion': unit.grams_conversion,
                            'is_bound': True
                        })
                        break
            else:
                available.append({
                    'id': unit.id,
                    'name': unit.name,
                    'unit_type': unit.unit_type,
                    'grams_conversion': unit.grams_conversion,
                    'is_bound': False
                })
        return available

    def get_grams_conversion_for_unit(self, unit_id):
        for ui in self.unit_associations:
            if ui.unit_id == unit_id:
                return ui.grams_override
        unit = Unit.query.get(unit_id)
        if unit:
            return unit.grams_conversion
        return None

class UnitIngredient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey('unit.id'), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('ingredient.id'), nullable=False)
    grams_override = db.Column(db.Float, nullable=False)

    __table_args__ = (db.UniqueConstraint('unit_id', 'ingredient_id'),)

class Recipe(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    instructions = db.Column(db.Text, nullable=True)
    is_draft = db.Column(db.Boolean, default=True)
    portions = db.Column(db.Float, default=1)
    languages = db.Column(db.String(50), default='en')
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipe_ingredients = db.relationship('RecipeIngredient', backref='recipe', cascade="all, delete-orphan", lazy=True)
    steps = db.relationship('RecipeStep', backref='recipe', cascade="all, delete-orphan", lazy=True, order_by='RecipeStep.step_number')
    translations = db.relationship('RecipeTranslation', backref='recipe', cascade="all, delete-orphan", lazy=True)

    @property
    def language_list(self):
        return self.languages.split(',') if self.languages else ['en']
    
    def get_title(self, preferred_lang=None):
        if preferred_lang is None:
            preferred_lang = session.get('lang', 'en')
        langs = self.language_list
        if preferred_lang != 'en' and preferred_lang in langs:
            trans = RecipeTranslation.query.filter_by(recipe_id=self.id, language=preferred_lang).first()
            if trans:
                return trans.title
        return self.title
    
    def get_description(self, preferred_lang=None):
        if preferred_lang is None:
            preferred_lang = session.get('lang', 'en')
        langs = self.language_list
        if preferred_lang != 'en' and preferred_lang in langs:
            trans = RecipeTranslation.query.filter_by(recipe_id=self.id, language=preferred_lang).first()
            if trans and trans.description:
                return trans.description
        return self.description

    @property
    def total_weight_grams(self):
        total = 0
        for ri in self.recipe_ingredients:
            total += ri.amount_grams
        return round(total, 2)

    @property
    def all_ingredients(self):
        return [ri for ri in self.recipe_ingredients]

    @property
    def aggregated_ingredients(self):
        aggregated = {}
        for ri in self.recipe_ingredients:
            ing_id = ri.ingredient_id
            unit_grams = 1.0
            if ri.unit:
                if ri.unit.unit_type == 'volume':
                    density = ri.ingredient.density if ri.ingredient.density else 1.0
                    unit_grams = ri.unit.grams_conversion * density
                else:
                    unit_grams = ri.unit.grams_conversion
            if ing_id not in aggregated:
                aggregated[ing_id] = {
                    'ingredient': ri.ingredient,
                    'total_amount': 0,
                    'total_grams': 0,
                    'display_unit': ri.display_unit,
                    'original_unit_id': ri.unit_id,
                    'original_unit_grams': unit_grams,
                    'steps': [],
                    'available_units': ri.ingredient.get_available_units()
                }
            aggregated[ing_id]['total_amount'] += ri.amount
            aggregated[ing_id]['total_grams'] += ri.amount_grams
            if ri.step_number is not None:
                aggregated[ing_id]['steps'].append({
                    'step_number': ri.step_number,
                    'amount': ri.amount,
                    'display_unit': ri.display_unit,
                    'grams': ri.amount_grams,
                    'original_unit_grams': unit_grams,
                    'available_units': ri.ingredient.get_available_units()
                })
        return list(aggregated.values())

    @property
    def steps_dict(self):
        steps = {}
        for ri in self.recipe_ingredients:
            if ri.step_number is not None:
                if ri.step_number not in steps:
                    steps[ri.step_number] = []
                steps[ri.step_number].append(ri)
        return dict(sorted(steps.items()))

class RecipeIngredient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey('recipe.id'), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('ingredient.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey('unit.id'), nullable=True)
    step_number = db.Column(db.Integer, nullable=True)
    ingredient = db.relationship('Ingredient')
    unit = db.relationship('Unit')

    @property
    def amount_grams(self):
        if not self.unit:
            return self.amount
        for ui in self.unit.ingredient_units:
            if ui.ingredient_id == self.ingredient_id:
                return round(self.amount * ui.grams_override, 2)
        if self.unit.unit_type == 'volume':
            if self.ingredient.density_unit == 'g/unit':
                return round(self.amount * self.ingredient.grams_per_unit, 2)
            density = self.ingredient.density if self.ingredient.density else 1.0
            return round(self.amount * self.unit.grams_conversion * density, 2)
        return round(self.amount * self.unit.grams_conversion, 2)

    @property
    def display_unit(self):
        return self.unit.name if self.unit else 'g'

class RecipeStep(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey('recipe.id'), nullable=False)
    step_number = db.Column(db.Integer, nullable=False)
    instruction = db.Column(db.Text, nullable=False)

class RecipeTranslation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey('recipe.id'), nullable=False)
    language = db.Column(db.String(10), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    instructions = db.Column(db.Text, nullable=True)

    __table_args__ = (db.UniqueConstraint('recipe_id', 'language'),)

class RecipeStepTranslation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey('recipe.id'), nullable=False)
    step_number = db.Column(db.Integer, nullable=False)
    language = db.Column(db.String(10), nullable=False)
    instruction = db.Column(db.Text, nullable=False)

    __table_args__ = (db.UniqueConstraint('recipe_id', 'step_number', 'language'),)

class ShelfItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('ingredient.id'), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey('unit.id'), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    best_before = db.Column(db.Date, nullable=True)
    mhd = db.Column(db.Date, nullable=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ingredient = db.relationship('Ingredient', foreign_keys=[ingredient_id])
    unit = db.relationship('Unit', foreign_keys=[unit_id])

    @property
    def amount_grams(self):
        if not self.unit:
            return self.amount
        for ui in self.unit.ingredient_units:
            if ui.ingredient_id == self.ingredient_id:
                return round(self.amount * ui.grams_override, 2)
        if self.unit.unit_type == 'volume':
            density = self.ingredient.density if self.ingredient.density else 1.0
            return round(self.amount * self.unit.grams_conversion * density, 2)
        return round(self.amount * self.unit.grams_conversion, 2)

class ShoppingCartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('ingredient.id'), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey('unit.id'), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ingredient = db.relationship('Ingredient', foreign_keys=[ingredient_id])
    unit = db.relationship('Unit', foreign_keys=[unit_id])

    __table_args__ = (db.UniqueConstraint('ingredient_id', 'unit_id', 'creator_id'),)

    @property
    def amount_grams(self):
        if not self.unit:
            return self.amount
        for ui in self.unit.ingredient_units:
            if ui.ingredient_id == self.ingredient_id:
                return round(self.amount * ui.grams_override, 2)
        if self.unit.unit_type == 'volume':
            density = self.ingredient.density if self.ingredient.density else 1.0
            return round(self.amount * self.unit.grams_conversion * density, 2)
        return round(self.amount * self.unit.grams_conversion, 2)

@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except (ValueError, TypeError):
        return None

@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = session.get("_csrf_token")
        if not token or token != request.form.get("_csrf_token"):
            if request.is_json:
                abort(400)
            flash('Invalid CSRF token', 'danger')
            # Skip CSRF check for forms without token (compatibility)
            if not request.form.get("_csrf_token"):
                return None
            return redirect(request.full_path)

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

@app.before_request
def handle_language():
    lang = request.args.get('lang')
    if lang and lang in ('en', 'de', 'ru'):
        session['lang'] = lang
    if 'lang' not in session:
        session['lang'] = 'en'

@app.context_processor
def inject_translations():
    translations_dict = {
        'en': {
            'home': 'Home', 'shelf': 'Shelf', 'cart': 'Cart', 'add_recipe': 'Add Recipe',
            'add_ingredient': 'Add Ingredient', 'add_unit': 'Add Unit', 'login': 'Login',
            'register': 'Register', 'logout': 'Logout', 'search': 'Search', 'all_recipes': 'All Recipes',
            'shopping_list': 'Shopping List', 'my_shelf': 'My Shelf', 'download': 'Download Recipe',
            'check_shelf': 'Check Shelf', 'add_missing': 'Add Missing to Cart', 'use_from_shelf': 'Use from Shelf',
            'ingredients': 'Ingredients', 'instructions': 'Instructions', 'notes': 'Notes', 'portions': 'Portions',
            'actions': 'Actions', 'tips': 'Tips', 'export': 'Export', 'copy_list': 'Copy List',
            'discover_recipes': 'Discover Delicious Recipes', 'find_share': 'Find and share your favorite culinary creations',
            'search_recipes': 'Search recipes...', 'search_results': 'Search Results', 'no_recipes': 'No recipes found',
            'try_different': 'Try a different search term', 'create_first': 'Start by creating your first recipe!',
            'view_recipe': 'View Recipe', 'edit_recipe': 'Edit Recipe', 'draft': 'Draft', 'no_description': 'No description',
            'step': 'Step', 'ingredients_for_step': 'Ingredients for this step', 'convert_to': 'Convert to', 'original': 'original',
            'on_shelf': '✓ On shelf', 'partial': '⚠ Partial', 'missing': '✗ Missing', 'have': 'have',
            'shelf_check': 'Shelf Check', 'close': 'Close', 'save': 'Save', 'cancel': 'Cancel',
            'delete': 'Delete', 'confirm_delete': 'Confirm Delete', 'item_added': 'Item added to shelf!',
            'item_removed': 'Item removed', 'cart_empty': 'Cart is empty', 'add_to_cart': 'Add to cart',
            'required': 'required', 'title_required': 'Title is required', 'amount_required': 'Amount required',
            'ingredient_required': 'Ingredient and amount required', 'name_required': 'Name is required',
            'invalid_amount': 'Invalid amount', 'positive_number': 'must be a positive number',
            'username': 'Username', 'password': 'Password', 'login_failed': 'Login failed.',
            'register_success': 'Registration successful', 'username_taken': 'Username taken',
            'alphanumeric': 'Username must be alphanumeric', 'min_chars': 'Username must be 3-20 characters',
            'min_password': 'Password must be at least 8 characters',
            'title': 'Title', 'description': 'Description', 'is_draft': 'Save as draft',
            'add_more_ingredients': 'Add More Ingredients', 'save_recipe': 'Save Recipe',
            'update_recipe': 'Update Recipe', 'ingredient_name': 'Ingredient Name', 'density': 'Density',
            'grams_per_unit': 'Grams per unit', 'unit_name': 'Unit Name', 'preferred_unit': 'Preferred Unit',
            'comment': 'Comment', 'unit_type': 'Unit Type', 'mass': 'Mass', 'volume': 'Volume', 'count': 'Count',
            'grams_conversion': 'Grams Conversion', 'bound_to_ingredient': 'Bind to specific ingredient',
            'available_units': 'Available Units', 'add_item': 'Add Item', 'quantity': 'Quantity', 'best_before': 'Best Before',
            'shopping_list_title': 'Shopping List', 'copy_to_clipboard': 'Copy to Clipboard',
            'recipe_created': 'Recipe created!', 'recipe_updated': 'Recipe updated!',
            'added_to_cart': 'Added to cart', 'used_from_shelf': 'Used from shelf', 'not_on_shelf': 'Not on shelf',
            'all_available': 'All ingredients available on shelf!',
            'print_recipe': 'Print Recipe', 'english': 'English', 'german': 'German', 'russian': 'Russian',
        },
        'de': {
            'home': 'Startseite', 'shelf': 'Vorrat', 'cart': 'Einkaufswagen', 'add_recipe': 'Rezept hinzufügen',
            'add_ingredient': 'Zutat hinzufügen', 'add_unit': 'Einheit hinzufügen', 'login': 'Anmelden',
            'register': 'Registrieren', 'logout': 'Abmelden', 'search': 'Suchen', 'all_recipes': 'Alle Rezepte',
            'shopping_list': 'Einkaufsliste', 'my_shelf': 'Mein Vorrat', 'download': 'Rezept herunterladen',
            'check_shelf': 'Vorrat prüfen', 'add_missing': 'Fehlendes hinzufügen', 'use_from_shelf': 'Aus Vorrat nehmen',
            'ingredients': 'Zutaten', 'instructions': 'Anleitung', 'notes': 'Notizen', 'portions': 'Portionen',
            'actions': 'Aktionen', 'tips': 'Tipps', 'export': 'Exportieren', 'copy_list': 'Liste kopieren',
            'discover_recipes': 'Entdecken Sie köstliche Rezepte', 'find_share': 'Teilen Sie Ihre Lieblingsgerichte',
            'search_recipes': 'Rezepte suchen...', 'search_results': 'Suchergebnisse', 'no_recipes': 'Keine Rezepte gefunden',
            'try_different': 'Versuchen Sie einen anderen Suchbegriff', 'create_first': 'Erstellen Sie Ihr erstes Rezept!',
            'view_recipe': 'Rezept ansehen', 'edit_recipe': 'Rezept bearbeiten', 'draft': 'Entwurf', 'no_description': 'Keine Beschreibung',
            'step': 'Schritt', 'ingredients_for_step': 'Zutaten für diesen Schritt', 'convert_to': 'Umrechnen zu', 'original': 'Original',
            'on_shelf': '✓ Im Vorrat', 'partial': '⚠ Teilweise', 'missing': '✗ Fehlt', 'have': 'haben',
            'shelf_check': 'Vorratsprüfung', 'close': 'Schließen', 'save': 'Speichern', 'cancel': 'Abbrechen',
            'delete': 'Löschen', 'confirm_delete': 'Löschen bestätigen', 'item_added': 'Artikel zum Vorrat hinzugefügt!',
            'item_removed': 'Artikel entfernt', 'cart_empty': 'Warenkorb ist leer', 'add_to_cart': 'In den Warenkorb',
            'required': 'erforderlich', 'title_required': 'Titel ist erforderlich', 'amount_required': 'Menge erforderlich',
            'ingredient_required': 'Zutat und Menge erforderlich', 'name_required': 'Name ist erforderlich',
            'invalid_amount': 'Ungültige Menge', 'positive_number': 'muss eine positive Zahl sein',
            'username': 'Benutzername', 'password': 'Passwort', 'login_failed': 'Anmeldung fehlgeschlagen.',
            'register_success': 'Registrierung erfolgreich', 'username_taken': 'Benutzername bereits vergeben',
            'alphanumeric': 'Benutzername muss alphanumerisch sein', 'min_chars': 'Benutzername muss 3-20 Zeichen haben',
            'min_password': 'Passwort muss mindestens 8 Zeichen haben',
            'title': 'Titel', 'description': 'Beschreibung', 'is_draft': 'Als Entwurf speichern',
            'add_more_ingredients': 'Weitere Zutaten hinzufügen', 'save_recipe': 'Rezept speichern',
            'update_recipe': 'Rezept aktualisieren', 'ingredient_name': 'Zutatenname', 'density': 'Dichte',
            'grams_per_unit': 'Gramm pro Einheit', 'unit_name': 'Einheitsname', 'preferred_unit': 'Bevorzugte Einheit',
            'comment': 'Kommentar', 'unit_type': 'Einheitstyp', 'mass': 'Masse', 'volume': 'Volumen', 'count': 'Stück',
            'grams_conversion': 'Gramm-Umrechnung', 'bound_to_ingredient': 'An bestimmte Zutat binden',
            'available_units': 'Verfügbare Einheiten', 'add_item': 'Artikel hinzufügen', 'quantity': 'Menge',
            'best_before': 'Mindestens haltbar bis', 'shopping_list_title': 'Einkaufsliste',
            'copy_to_clipboard': 'In Zwischenablage kopieren',
            'recipe_created': 'Rezept erstellt!', 'recipe_updated': 'Rezept aktualisiert!',
            'added_to_cart': 'Zum Warenkorb hinzugefügt', 'used_from_shelf': 'Vom Vorrat verwendet',
            'not_on_shelf': 'Nicht im Vorrat', 'all_available': 'Alle Zutaten im Vorrat!',
            'print_recipe': 'Rezept drucken', 'english': 'Englisch', 'german': 'Deutsch', 'russian': 'Russisch',
        },
        'ru': {
            'home': 'Главная', 'shelf': 'Кладовая', 'cart': 'Корзина', 'add_recipe': 'Добавить рецепт',
            'add_ingredient': 'Добавить ингредиент', 'add_unit': 'Добавить единицу', 'login': 'Войти',
            'register': 'Регистрация', 'logout': 'Выйти', 'search': 'Поиск', 'all_recipes': 'Все рецепты',
            'shopping_list': 'Список покупок', 'my_shelf': 'Моя кладовая', 'download': 'Скачать рецепт',
            'check_shelf': 'Проверить кладовую', 'add_missing': 'Добавить недостающее', 'use_from_shelf': 'Взять с кладовой',
            'ingredients': 'Ингредиенты', 'instructions': 'Инструкции', 'notes': 'Заметки', 'portions': 'Порции',
            'actions': 'Действия', 'tips': 'Советы', 'export': 'Экспорт', 'copy_list': 'Копировать список',
            'discover_recipes': 'Откройте для себя вкусные рецепты', 'find_share': 'Делитесь своими любимыми блюдами',
            'search_recipes': 'Поиск рецептов...', 'search_results': 'Результаты поиска', 'no_recipes': 'Рецепты не найдены',
            'try_different': 'Попробуйте другой поисковый запрос', 'create_first': 'Создайте свой первый рецепт!',
            'view_recipe': 'Посмотреть рецепт', 'edit_recipe': 'Редактировать рецепт', 'draft': 'Черновик', 'no_description': 'Нет описания',
            'step': 'Шаг', 'ingredients_for_step': 'Ингредиенты для этого шага', 'convert_to': 'Конвертировать в', 'original': 'оригинал',
            'on_shelf': '✓ На полке', 'partial': '⚠ Частично', 'missing': '✗ Отсутствует', 'have': 'есть',
            'shelf_check': 'Проверка кладовой', 'close': 'Закрыть', 'save': 'Сохранить', 'cancel': 'Отмена',
            'delete': 'Удалить', 'confirm_delete': 'Подтвердить удаление', 'item_added': 'Добавлено на полку!',
            'item_removed': 'Удалено', 'cart_empty': 'Корзина пуста', 'add_to_cart': 'В корзину',
            'required': 'обязательно', 'title_required': 'Название обязательно', 'amount_required': 'Количество обязательно',
            'ingredient_required': 'Ингредиент и количество обязательны', 'name_required': 'Имя обязательно',
            'invalid_amount': 'Неверное количество', 'positive_number': 'должно быть положительным числом',
            'username': 'Имя пользователя', 'password': 'Пароль', 'login_failed': 'Вход не выполнен.',
            'register_success': 'Регистрация успешна', 'username_taken': 'Имя пользователя занято',
            'alphanumeric': 'Имя пользователя должно быть буквенно-цифровым', 'min_chars': 'Имя пользователя должно быть 3-20 символов',
            'min_password': 'Пароль должен содержать минимум 8 символов',
            'title': 'Название', 'description': 'Описание', 'is_draft': 'Сохранить как черновик',
            'add_more_ingredients': 'Добавить ингредиенты', 'save_recipe': 'Сохранить рецепт',
            'update_recipe': 'Обновить рецепт', 'ingredient_name': 'Название ингредиента', 'density': 'Плотность',
            'grams_per_unit': 'Грамм н�� единицу', 'unit_name': 'Название единицы', 'preferred_unit': 'Предпочитаемая единица',
            'comment': 'Комментарий', 'unit_type': 'Тип единицы', 'mass': 'Масса', 'volume': 'Объём', 'count': 'Штука',
            'grams_conversion': 'Конвертация в граммы', 'bound_to_ingredient': 'Связать с ингредиентом',
            'available_units': 'Доступные единицы', 'add_item': 'Добавить', 'quantity': 'Количество',
            'best_before': 'Годен до', 'shopping_list_title': 'Список покупок',
            'copy_to_clipboard': 'Копировать в буфер',
            'recipe_created': 'Рецепт создан!', 'recipe_updated': 'Рецепт обновлён!',
            'added_to_cart': 'Добавлено в корзину', 'used_from_shelf': 'Взято с полки', 'not_on_shelf': 'Нет на полке',
            'all_available': 'Все ингредиенты есть на полке!',
            'print_recipe': 'Печать рецепта', 'english': 'Английский', 'german': 'Немецкий', 'russian': 'Русский',
}
}
    
    ingredient_translations = {
        'Mehl (Type 405)': {'de': 'Mehl (Type 405)', 'ru': 'Мука (Тип 405)'},
        'Backkakao': {'de': 'Backkakao', 'ru': 'Какао-порошок'},
        'Zucker': {'de': 'Zucker', 'ru': 'Сахар'},
        'Speiseöl': {'de': 'Speiseöl', 'ru': 'Растительное масло'},
        'Backpulver': {'de': 'Backpulver', 'ru': 'Разрыхлитель'},
        'Wasser': {'de': 'Wasser', 'ru': 'Вода'},
        'Vanilleextrakt': {'de': 'Vanilleextrakt', 'ru': 'Ванильный экстракт'},
        'Vanille': {'de': 'Vanille', 'ru': 'Ваниль'},
        'Salz': {'de': 'Salz', 'ru': 'Соль'},
        'Butter': {'de': 'Butter', 'ru': 'Масло сливочное'},
        'Eier': {'de': 'Eier', 'ru': 'Яйца'},
        'Milch': {'de': 'Milch', 'ru': 'Молоко'},
        'Sahne': {'de': 'Sahne', 'ru': 'Сливки'},
    }
    
    unit_translations = {
        'g': {'de': 'g', 'ru': 'г'},
        'kg': {'de': 'kg', 'ru': 'кг'},
        'mL': {'de': 'mL', 'ru': 'мл'},
        'L': {'de': 'L', 'ru': 'л'},
        'EL': {'de': 'EL', 'ru': 'ст.л.'},
        'TL': {'de': 'TL', 'ru': 'ч.л.'},
    }

    def auto_translate_text(text, target_lang):
        if not text or target_lang == 'en':
            return text
        result = text
        for orig, trans in ingredient_translations.items():
            if target_lang in trans:
                result = result.replace(orig, trans[target_lang])
        for orig, trans in unit_translations.items():
            if target_lang in trans and orig != target_lang and orig not in result:
                result = result.replace(orig, trans[target_lang])
        return result
    
    current_lang = session.get('lang', 'en')
    
    def translate_content(text):
        if not text:
            return text
        result = text
        for orig, trans in ingredient_translations.items():
            if current_lang in trans:
                result = result.replace(orig, trans[current_lang])
        for orig, trans in unit_translations.items():
            if current_lang in trans and orig != current_lang:
                result = result.replace(orig, trans[current_lang])
        return result

    return dict(
        t=lambda key: translations_dict.get(current_lang, translations_dict['en']).get(key, key),
        lang=current_lang,
        languages=[('en', 'EN'), ('de', 'DE'), ('ru', 'RU')],
        translate=translate_content,
        request=request
    )

# --- Routes ---

@app.route("/")
def home():
    search_query = request.args.get('q', '')
    
    if current_user.is_authenticated:
        recipes = Recipe.query.filter(
            (Recipe.is_draft == False) | (Recipe.creator_id == current_user.id)
        )
    else:
        recipes = Recipe.query.filter_by(is_draft=False)
    
    if search_query:
        recipes = recipes.filter(Recipe.title.ilike(f'%{search_query}%'))
    
    recipes = recipes.order_by(Recipe.id.desc()).all()
    return render_template('home.html', recipes=recipes, search_query=search_query)

@app.route("/recipe/<int:recipe_id>")
def recipe_detail(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    if recipe.is_draft and (not current_user.is_authenticated or recipe.creator_id != current_user.id):
        abort(404)
    
    view_lang = request.args.get('lang')
    if not view_lang:
        view_lang = session.get('lang', 'en')
    
    langs = recipe.language_list
    
    if view_lang != 'en' and view_lang not in langs:
        view_lang = 'en'
    
    trans = None
    step_trans = {}
    if view_lang != 'en':
        trans = RecipeTranslation.query.filter_by(recipe_id=recipe.id, language=view_lang).first()
        if trans:
            for st in RecipeStepTranslation.query.filter_by(recipe_id=recipe.id, language=view_lang).all():
                step_trans[st.step_number] = st.instruction
    
    return render_template('recipe_detail.html', recipe=recipe, trans=trans, step_trans=step_trans, view_lang=view_lang)

@app.route("/recipe/new", methods=['GET', 'POST'])
@login_required
def add_recipe():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        instructions = request.form.get('instructions', '').strip()
        is_draft = True if request.form.get('is_draft') else False
        
        if not title:
            flash('Title is required', 'danger')
            all_ingredients = [{'id': i.id, 'name': i.name, 'preferred_unit_id': i.preferred_unit_id} for i in Ingredient.query.all()]
            all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
            return render_template('add_recipe.html', all_ingredients=all_ingredients, all_units=all_units)
        
        portions = 1
        if request.form.get('portions'):
            try:
                portions = float(request.form.get('portions'))
                if portions <= 0:
                    raise ValueError()
            except ValueError:
                flash('Portions must be a positive number', 'danger')
                all_ingredients = [{'id': i.id, 'name': i.name, 'preferred_unit_id': i.preferred_unit_id} for i in Ingredient.query.all()]
                all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
                return render_template('add_recipe.html', all_ingredients=all_ingredients, all_units=all_units)

        new_recipe = Recipe(
            title=title,
            description=description,
            instructions=instructions,
            is_draft=is_draft,
            portions=portions,
            languages='en',
            creator_id=current_user.id
        )
        db.session.add(new_recipe)
        db.session.flush()

        ing_ids = request.form.getlist('ing_id[]')
        amounts = request.form.getlist('amount[]')
        unit_ids = request.form.getlist('unit_id[]')
        step_nums = request.form.getlist('step_num[]')

        for i in range(len(ing_ids)):
            if ing_ids[i] and i < len(amounts) and i < len(step_nums):
                step_num_str = step_nums[i] if i < len(step_nums) else None
                unit_id_val = unit_ids[i] if i < len(unit_ids) else None
                ri = RecipeIngredient(
                    recipe_id=new_recipe.id,
                    ingredient_id=int(ing_ids[i]),
                    amount=float(amounts[i]),
                    unit_id=int(unit_id_val) if unit_id_val and unit_id_val.strip() else None,
                    step_number=int(step_num_str) if step_num_str and step_num_str.strip() else None
                )
                db.session.add(ri)

        step_instructions = request.form.getlist('step_instruction[]')
        for i, instr in enumerate(step_instructions):
            if instr and instr.strip():
                step = RecipeStep(
                    recipe_id=new_recipe.id,
                    step_number=i + 1,
                    instruction=instr.strip()
                )
                db.session.add(step)

        try:
            db.session.commit()
            flash('Recipe created!', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating recipe: {str(e)}', 'danger')
            all_ingredients = [{'id': i.id, 'name': i.name, 'preferred_unit_id': i.preferred_unit_id} for i in Ingredient.query.all()]
            all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
            return render_template('add_recipe.html', all_ingredients=all_ingredients, all_units=all_units)

    all_ingredients = [{'id': i.id, 'name': i.name, 'preferred_unit_id': i.preferred_unit_id} for i in Ingredient.query.all()]
    all_units = []
    for u in Unit.query.all():
        unit_data = {
            'id': u.id,
            'name': u.name,
            'unit_type': u.unit_type,
            'is_bound': u.is_bound,
            'ingredients': [ui.ingredient_id for ui in u.ingredient_units]
        }
        all_units.append(unit_data)
    return render_template('add_recipe.html', all_ingredients=all_ingredients, all_units=all_units)

@app.route("/recipe/<int:recipe_id>/edit", methods=['GET', 'POST'])
@login_required
def edit_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    if recipe.creator_id != current_user.id:
        flash('You cannot edit this recipe.', 'danger')
        return redirect(url_for('home'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required', 'danger')
            all_ingredients = [{'id': i.id, 'name': i.name, 'preferred_unit_id': i.preferred_unit_id} for i in Ingredient.query.all()]
            all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
            return render_template('edit_recipe.html', recipe=recipe, all_ingredients=all_ingredients, all_units=all_units)
        
        recipe.title = title
        recipe.description = request.form.get('description', '').strip()
        recipe.instructions = request.form.get('instructions', '').strip()
        recipe.is_draft = True if request.form.get('is_draft') else False
        
        portions = recipe.portions
        if request.form.get('portions'):
            try:
                portions = float(request.form.get('portions'))
                if portions <= 0:
                    raise ValueError()
            except ValueError:
                flash('Portions must be a positive number', 'danger')
                all_ingredients = [{'id': i.id, 'name': i.name, 'preferred_unit_id': i.preferred_unit_id} for i in Ingredient.query.all()]
                all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
                return render_template('edit_recipe.html', recipe=recipe, all_ingredients=all_ingredients, all_units=all_units)
        recipe.portions = portions

        RecipeIngredient.query.filter_by(recipe_id=recipe.id).delete()
        RecipeStep.query.filter_by(recipe_id=recipe.id).delete()

        ing_ids = request.form.getlist('ing_id[]')
        amounts = request.form.getlist('amount[]')
        unit_ids = request.form.getlist('unit_id[]')
        step_nums = request.form.getlist('step_num[]')

        for i in range(len(ing_ids)):
            if ing_ids[i] and i < len(amounts) and i < len(step_nums):
                step_num_str = step_nums[i] if i < len(step_nums) else None
                unit_id_val = unit_ids[i] if i < len(unit_ids) else None
                ri = RecipeIngredient(
                    recipe_id=recipe.id,
                    ingredient_id=int(ing_ids[i]),
                    amount=float(amounts[i]),
                    unit_id=int(unit_id_val) if unit_id_val and unit_id_val.strip() else None,
                    step_number=int(step_num_str) if step_num_str and step_num_str.strip() else None,
                    comment=comment_val
                )
                db.session.add(ri)

        step_instructions = request.form.getlist('step_instruction[]')
        for i, instr in enumerate(step_instructions):
            if instr and instr.strip():
                step = RecipeStep(
                    recipe_id=recipe.id,
                    step_number=i + 1,
                    instruction=instr.strip()
                )
                db.session.add(step)

        try:
            db.session.commit()
            flash('Recipe updated!', 'success')
            return redirect(url_for('recipe_detail', recipe_id=recipe.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating recipe: {str(e)}', 'danger')
            all_ingredients = [{'id': i.id, 'name': i.name, 'preferred_unit_id': i.preferred_unit_id} for i in Ingredient.query.all()]
            all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
            return render_template('edit_recipe.html', recipe=recipe, all_ingredients=all_ingredients, all_units=all_units)

    all_ingredients = [{'id': i.id, 'name': i.name, 'preferred_unit_id': i.preferred_unit_id} for i in Ingredient.query.all()]
    all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
    return render_template('edit_recipe.html', recipe=recipe, all_ingredients=all_ingredients, all_units=all_units)

@app.route("/api/units/<int:ingredient_id>")
@login_required
def get_units_for_ingredient(ingredient_id):
    ingredient = Ingredient.query.get_or_404(ingredient_id)
    units = Unit.query.all()
    available = []
    for unit in units:
        if unit.is_bound:
            for ui in unit.ingredient_units:
                if ui.ingredient_id == ingredient_id:
                    available.append({'id': unit.id, 'name': unit.name, 'unit_type': unit.unit_type})
                    break
        else:
            available.append({'id': unit.id, 'name': unit.name, 'unit_type': unit.unit_type})
    return {'units': available}

@app.route("/login", methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('home'))
        flash('Login failed.', 'danger')
    return render_template('login.html')

@app.route("/register", methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if len(username) < 3 or len(username) > 20:
            flash('Username must be 3-20 characters', 'danger')
        elif not username.isalnum():
            flash('Username must be alphanumeric', 'danger')
        elif len(password) < 8:
            flash('Password must be at least 8 characters', 'danger')
        elif User.query.filter_by(username=username).first():
            flash('Username taken', 'danger')
        else:
            hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
            new_user = User(username=username, password=hashed_password)
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful', 'success')
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route("/ingredient/new", methods=['GET', 'POST'])
@login_required
def add_ingredient():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required', 'danger')
            return render_template('add_ingredient.html')
        
        density_type = request.form.get('density_type')
        density = None
        grams_per_unit = None
        unit_name = None
        preferred_unit_id = None
        
        if density_type == 'g/ml':
            density_val = request.form.get('density')
            if density_val:
                try:
                    density = float(density_val)
                    if density <= 0:
                        raise ValueError()
                except ValueError:
                    flash('Density must be a positive number', 'danger')
                    return render_template('add_ingredient.html')
        elif density_type == 'g/unit':
            grams_val = request.form.get('grams_per_unit')
            if grams_val:
                try:
                    grams_per_unit = float(grams_val)
                    if grams_per_unit <= 0:
                        raise ValueError()
                except ValueError:
                    flash('Grams per unit must be a positive number', 'danger')
                    return render_template('add_ingredient.html')
            unit_name = request.form.get('unit_name', '').strip()
        
        comment = request.form.get('comment') or None
        
        new_ingredient = Ingredient(
            name=name,
            density=density,
            density_unit=density_type,
            grams_per_unit=grams_per_unit,
            unit_name=unit_name,
            comment=comment,
            creator_id=current_user.id
        )
        db.session.add(new_ingredient)
        db.session.flush()
        
        if density_type == 'g/unit' and unit_name:
            new_unit = Unit(
                name=unit_name,
                unit_type='count',
                grams_conversion=grams_per_unit,
                is_bound=True,
                creator_id=current_user.id
            )
            db.session.add(new_unit)
            db.session.flush()
            
            new_ingredient.preferred_unit_id = new_unit.id
            
            ui = UnitIngredient(
                unit_id=new_unit.id,
                ingredient_id=new_ingredient.id,
                grams_override=grams_per_unit
            )
            db.session.add(ui)
        
        try:
            db.session.commit()
            flash('Ingredient added!', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding ingredient: {str(e)}', 'danger')
    return render_template('add_ingredient.html')

@app.route("/unit/new", methods=['GET', 'POST'])
@login_required
def add_unit():
    all_ingredients = [{'id': i.id, 'name': i.name, 'preferred_unit_id': i.preferred_unit_id} for i in Ingredient.query.all()]
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        unit_type = request.form.get('unit_type')
        grams_conversion_val = request.form.get('grams_conversion')
        
        if not name:
            flash('Name is required', 'danger')
            return render_template('add_unit.html', all_ingredients=all_ingredients)
        
        if not unit_type or unit_type not in ('mass', 'volume', 'count'):
            flash('Invalid unit type', 'danger')
            return render_template('add_unit.html', all_ingredients=all_ingredients)
        
        try:
            grams_conversion = float(grams_conversion_val)
            if grams_conversion <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            flash('Grams conversion must be a positive number', 'danger')
            return render_template('add_unit.html', all_ingredients=all_ingredients)
        
        is_bound = True if request.form.get('is_bound') else False
        
        new_unit = Unit(
            name=name,
            unit_type=unit_type,
            grams_conversion=grams_conversion,
            is_bound=is_bound,
            creator_id=current_user.id
        )
        db.session.add(new_unit)
        db.session.flush()
        
        ingredient_ids = request.form.getlist('ingredient_id[]')
        grams_overrides = request.form.getlist('grams_override[]')
        
        for i in range(len(ingredient_ids)):
            if ingredient_ids[i] and i < len(grams_overrides) and grams_overrides[i]:
                ui = UnitIngredient(
                    unit_id=new_unit.id,
                    ingredient_id=int(ingredient_ids[i]),
                    grams_override=float(grams_overrides[i])
                )
                db.session.add(ui)
        
        try:
            db.session.commit()
            flash('Unit added!', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding unit: {str(e)}', 'danger')
            return render_template('add_unit.html', all_ingredients=all_ingredients)
    
    return render_template('add_unit.html', all_ingredients=all_ingredients)

@app.route("/shelf", methods=['GET', 'POST'])
@login_required
def shelf():
    all_ingredients = [{'id': i.id, 'name': i.name} for i in Ingredient.query.all()]
    all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type} for u in Unit.query.filter_by(creator_id=current_user.id).all()]
    all_units += [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type} for u in Unit.query.filter_by(creator_id=None).all()]
    all_units = {u['id']: u for u in all_units}.values()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            ingredient_id = request.form.get('ingredient_id')
            amount = request.form.get('amount')
            unit_id = request.form.get('unit_id')
            mhd = request.form.get('mhd')
            
            if not ingredient_id or not amount:
                flash('Ingredient and amount required', 'danger')
            else:
                try:
                    amount = float(amount)
                    mhd_date = None
                    if mhd:
                        mhd_date = datetime.strptime(mhd, '%Y-%m-%d').date()
                    
                    item = ShelfItem(
                        ingredient_id=int(ingredient_id),
                        amount=amount,
                        unit_id=int(unit_id) if unit_id and unit_id.strip() else None,
                        mhd=mhd_date,
                        creator_id=current_user.id
                    )
                    db.session.add(item)
                    db.session.commit()
                    flash('Item added to shelf!', 'success')
                except ValueError:
                    flash('Invalid amount', 'danger')
        
        elif action == 'delete':
            item_id = request.form.get('item_id')
            item = ShelfItem.query.get(item_id)
            if item and item.creator_id == current_user.id:
                db.session.delete(item)
                db.session.commit()
                flash('Item removed', 'success')
    
    items = ShelfItem.query.filter_by(creator_id=current_user.id).all()
    today_date = date.today()
    return render_template('shelf.html', items=items, all_ingredients=all_ingredients, all_units=all_units, today_date=today_date)

@app.route("/shelf/add/<int:ingredient_id>", methods=['GET', 'POST'])
@login_required
def shelf_add_ingredient(ingredient_id):
    ingredient = Ingredient.query.get_or_404(ingredient_id)
    all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type} for u in Unit.query.filter_by(creator_id=current_user.id).all()]
    all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type} for u in Unit.query.filter_by(creator_id=None).all() if u not in all_units] + all_units
    
    if request.method == 'POST':
        amount = request.form.get('amount')
        unit_id = request.form.get('unit_id')
        mhd = request.form.get('mhd')
        
        if amount:
            try:
                amount = float(amount)
                mhd_date = None
                if mhd:
                    mhd_date = datetime.strptime(mhd, '%Y-%m-%d').date()
                
                item = ShelfItem(
                    ingredient_id=ingredient_id,
                    amount=amount,
                    unit_id=int(unit_id) if unit_id and unit_id.strip() else None,
                    mhd=mhd_date,
                    creator_id=current_user.id
                )
                db.session.add(item)
                db.session.commit()
                flash('Item added to shelf!', 'success')
                return redirect(url_for('shelf'))
            except ValueError:
                flash('Invalid amount', 'danger')
    
    return render_template('shelf_add.html', ingredient=ingredient, all_units=all_units)

@app.route("/shopping_cart", methods=['GET', 'POST'])
@login_required
def shopping_cart():
    all_ingredients = [{'id': i.id, 'name': i.name} for i in Ingredient.query.all()]
    all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type} for u in Unit.query.all()]
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            ingredient_id = request.form.get('ingredient_id')
            amount = request.form.get('amount')
            unit_id = request.form.get('unit_id')
            
            if not ingredient_id or not amount:
                flash('Ingredient and amount required', 'danger')
            else:
                try:
                    amount = float(amount)
                    existing = ShoppingCartItem.query.filter_by(
                        ingredient_id=int(ingredient_id),
                        unit_id=int(unit_id) if unit_id and unit_id.strip() else None,
                        creator_id=current_user.id
                    ).first()
                    
                    if existing:
                        existing.amount += amount
                    else:
                        item = ShoppingCartItem(
                            ingredient_id=int(ingredient_id),
                            amount=amount,
                            unit_id=int(unit_id) if unit_id and unit_id.strip() else None,
                            creator_id=current_user.id
                        )
                        db.session.add(item)
                    db.session.commit()
                    flash('Item added to cart!', 'success')
                except ValueError:
                    flash('Invalid amount', 'danger')
        
        elif action == 'delete':
            item_id = request.form.get('item_id')
            item = ShoppingCartItem.query.get(item_id)
            if item and item.creator_id == current_user.id:
                db.session.delete(item)
                db.session.commit()
                flash('Item removed', 'success')
        
        elif action == 'copy_clipboard':
            items = ShoppingCartItem.query.filter_by(creator_id=current_user.id).all()
            if items:
                lines = ["# Shopping List", ""]
                for item in items:
                    amount_str = f"{item.amount} {item.unit.name}" if item.unit else f"{item.amount}g"
                    lines.append(f"- [ ] {item.ingredient.name}: {amount_str}")
                clipboard_text = "\n".join(lines)
                return clipboard_text, 200, {'Content-Type': 'text/plain; charset=utf-8'}
            flash('Cart is empty', 'warning')
    
    items = ShoppingCartItem.query.filter_by(creator_id=current_user.id).all()
    return render_template('shopping_cart.html', items=items, all_ingredients=all_ingredients, all_units=all_units)

@app.route("/recipe/<int:recipe_id>/check_shelf")
@login_required
def check_shelf(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    shelf_items = {si.ingredient_id: si for si in ShelfItem.query.filter_by(creator_id=current_user.id).all()}
    
    results = []
    for ri in recipe.recipe_ingredients:
        shelf_item = shelf_items.get(ri.ingredient_id)
        if shelf_item:
            if shelf_item.amount_grams >= ri.amount_grams:
                results.append({'ingredient': ri.ingredient, 'status': 'has_enough', 'have': shelf_item.amount, 'need': ri.amount, 'unit': shelf_item.unit})
            else:
                results.append({'ingredient': ri.ingredient, 'status': 'partial', 'have': shelf_item.amount, 'need': ri.amount, 'unit': shelf_item.unit})
        else:
            results.append({'ingredient': ri.ingredient, 'status': 'missing', 'need': ri.amount})
    
    return {'results': results}

@app.route("/recipe/<int:recipe_id>/add_to_cart", methods=['POST'])
@login_required
def add_recipe_to_cart(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    shelf_items = {si.ingredient_id: si for si in ShelfItem.query.filter_by(creator_id=current_user.id).all()}
    cart_items = ShoppingCartItem.query.filter_by(creator_id=current_user.id).all()
    cart_lookup = {(ci.ingredient_id, ci.unit_id): ci for ci in cart_items}
    
    added = []
    for ri in recipe.recipe_ingredients:
        shelf_item = shelf_items.get(ri.ingredient_id)
        
        missing_amount = ri.amount
        if shelf_item and shelf_item.amount_grams >= ri.amount_grams:
            continue
        
        if shelf_item:
            missing_amount = ri.amount - (shelf_item.amount / ri.amount_grams * ri.amount_grams) if ri.amount_grams > 0 else 0
        
        if missing_amount <= 0:
            continue
        
        key = (ri.ingredient_id, ri.unit_id)
        existing = cart_lookup.get(key)
        
        if existing:
            existing.amount += missing_amount
        else:
            new_item = ShoppingCartItem(
                ingredient_id=ri.ingredient_id,
                amount=missing_amount,
                unit_id=ri.unit_id,
                creator_id=current_user.id
            )
            db.session.add(new_item)
        
        added.append(f"{ri.ingredient.name}: {missing_amount:.1f} {ri.unit.name if ri.unit else 'g'}")
    
    db.session.commit()
    
    if added:
        flash(f'Added to cart: {", ".join(added)}', 'success')
    else:
        flash('All ingredients available on shelf!', 'info')
    
    return redirect(url_for('shopping_cart'))

@app.route("/recipe/<int:recipe_id>/use_from_shelf", methods=['POST'])
@login_required
def use_from_shelf(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    shelf_items = {si.ingredient_id: si for si in ShelfItem.query.filter_by(creator_id=current_user.id).all()}
    
    used = []
    missing = []
    for ri in recipe.recipe_ingredients:
        shelf_item = shelf_items.get(ri.ingredient_id)
        
        if not shelf_item:
            missing.append(ri.ingredient.name)
            continue
        
        if shelf_item.amount_grams >= ri.amount_grams:
            shelf_item.amount -= ri.amount / ri.amount_grams * shelf_item.amount_grams if ri.amount_grams > 0 else ri.amount
            
            if shelf_item.amount <= 0:
                db.session.delete(shelf_item)
            
            used.append(f"{ri.ingredient.name}: {ri.amount} {ri.unit.name if ri.unit else 'g'}")
        else:
            missing.append(f"{ri.ingredient.name} (not enough)")
    
    db.session.commit()
    
    if used:
        flash(f'Used from shelf: {", ".join(used)}', 'success')
    if missing:
        flash(f'Not on shelf: {", ".join(missing)}', 'warning')
    
    return redirect(url_for('recipe_detail', recipe_id=recipe_id))

@app.route("/recipe/<int:recipe_id>/print")
def print_recipe(recipe_id):
    target_lang = request.args.get('lang', session.get('lang', 'en'))
    recipe = Recipe.query.get_or_404(recipe_id)
    
    def translate(text):
        if target_lang == 'en' or not text:
            return text
        
        ingredient_translations = {
            'Mehl (Type 405)': {'de': 'Mehl (Type 405)', 'ru': 'Мука (Тип 405)'},
            'Backkakao': {'de': 'Backkakao', 'ru': 'Какао-порошок'},
            'Zucker': {'de': 'Zucker', 'ru': 'Сахар'},
            'Speiseöl': {'de': 'Speiseöl', 'ru': 'Растительное масло'},
            'Backpulver': {'de': 'Backpulver', 'ru': 'Разрыхлитель'},
            'Wasser': {'de': 'Wasser', 'ru': 'Вода'},
            'Vanilleextrakt': {'de': 'Vanilleextrakt', 'ru': 'Ванильный экстракт'},
            'Vanille': {'de': 'Vanille', 'ru': 'Ваниль'},
        }
        
        for orig, trans in ingredient_translations.items():
            if target_lang in trans:
                text = text.replace(orig, trans[target_lang])
        
        if target_lang == 'ru':
            text = text.replace('g', 'г').replace('ml', 'мл').replace('TL', 'ч.л.').replace('EL', 'ст.л.')
            text = text.replace('Portions:', 'Порции:').replace('Ingredients', 'Ингредиенты')
            text = text.replace('Instructions', 'Инструкции').replace('Notes', 'Заметки')
            text = text.replace('Step', 'Шаг')
        elif target_lang == 'de':
            text = text.replace('Portions:', 'Portionen:').replace('Ingredients', 'Zutaten')
            text = text.replace('Instructions', 'Anleitung').replace('Notes', 'Notizen')
            text = text.replace('Step', 'Schritt')
        
        return text
    
    title = translate(recipe.title)
    description = translate(recipe.description) if recipe.description else ''
    instructions = translate(recipe.instructions) if recipe.instructions else ''
    
    pdf_content = f"""# {title}

{description}

**Portions:** {recipe.portions}

---

## Ingredients

"""
    for ri in recipe.recipe_ingredients:
        ing_name = translate(ri.ingredient.name)
        amount_str = f"{ri.amount} {ri.unit.name}" if ri.unit else f"{ri.amount}g"
        pdf_content += f"- {ing_name}: {amount_str}\n"
    
    if recipe.steps:
        pdf_content += "\n## Instructions\n\n"
        for step in recipe.steps:
            instruction = translate(step.instruction)
            pdf_content += f"{step.step_number}. {instruction}\n\n"
    
    if recipe.instructions:
        pdf_content += f"\n{instructions}\n"
    
    filename = f"{title.replace(' ', '_')}.md"
    return pdf_content, 200, {
        'Content-Type': 'text/markdown; charset=utf-8',
        'Content-Disposition': f'attachment; filename="{filename}"'
    }

@app.route("/recipe/<int:recipe_id>/translate", methods=['GET', 'POST'])
@login_required
def manage_translation(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    if recipe.creator_id != current_user.id:
        flash('You cannot translate this recipe.', 'danger')
        return redirect(url_for('home'))
    
    target_lang = request.args.get('lang', 'de')
    if target_lang not in ('de', 'ru'):
        target_lang = 'de'
    
    existing = RecipeTranslation.query.filter_by(recipe_id=recipe.id, language=target_lang).first()
    existing_steps = {}
    if existing:
        for st in RecipeStepTranslation.query.filter_by(recipe_id=recipe.id, language=target_lang).all():
            existing_steps[st.step_number] = st.instruction
    else:
        existing_steps = {s.step_number: auto_translate_text(s.instruction, target_lang) for s in recipe.steps}
    
    if not existing:
        auto_title = auto_translate_text(recipe.title, target_lang) if target_lang != 'en' else ''
        auto_desc = auto_translate_text(recipe.description, target_lang) if recipe.description else ''
        auto_instructions = auto_translate_text(recipe.instructions, target_lang) if recipe.instructions else ''
        
        if auto_title != '' and auto_title != recipe.title:
            existing = type('RecipeTranslation', (), {'title': auto_title, 'description': auto_desc, 'instructions': auto_instructions})()
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        instructions = request.form.get('instructions', '').strip()
        
        if not title:
            flash('Title is required', 'danger')
            return render_template('translate_recipe.html', recipe=recipe, target_lang=target_lang, existing=existing, existing_steps=existing_steps)
        
        if existing:
            existing.title = title
            existing.description = description
            existing.instructions = instructions
        else:
            new_trans = RecipeTranslation(
                recipe_id=recipe.id,
                language=target_lang,
                title=title,
                description=description,
                instructions=instructions
            )
            db.session.add(new_trans)
            
            langs = recipe.language_list
            if target_lang not in langs:
                langs.append(target_lang)
                recipe.languages = ','.join(langs)
        
        RecipeStepTranslation.query.filter_by(recipe_id=recipe.id, language=target_lang).delete()
        
        step_instructions = request.form.getlist('step_instruction[]')
        for i, instr in enumerate(step_instructions):
            if instr and instr.strip():
                step_trans = RecipeStepTranslation(
                    recipe_id=recipe.id,
                    step_number=i + 1,
                    language=target_lang,
                    instruction=instr.strip()
                )
                db.session.add(step_trans)
        
        try:
            db.session.commit()
            flash(f'Translation {target_lang.upper()} saved!', 'success')
            return redirect(url_for('recipe_detail', recipe_id=recipe.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving translation: {str(e)}', 'danger')
    
    return render_template('translate_recipe.html', recipe=recipe, target_lang=target_lang, existing=existing, existing_steps=existing_steps)

@app.route("/recipe/<int:recipe_id>/delete_translation/<lang_code>")
@login_required
def delete_translation(recipe_id, lang_code):
    recipe = Recipe.query.get_or_404(recipe_id)
    if recipe.creator_id != current_user.id:
        flash('You cannot delete this translation.', 'danger')
        return redirect(url_for('home'))
    
    if lang_code == 'en':
        flash('Cannot delete original language', 'danger')
        return redirect(url_for('recipe_detail', recipe_id=recipe.id))
    
    RecipeTranslation.query.filter_by(recipe_id=recipe.id, language=lang_code).delete()
    RecipeStepTranslation.query.filter_by(recipe_id=recipe.id, language=lang_code).delete()
    
    langs = recipe.language_list
    if lang_code in langs:
        langs.remove(lang_code)
        recipe.languages = ','.join(langs)
    
    db.session.commit()
    flash(f'Translation {lang_code.upper()} deleted', 'success')
    return redirect(url_for('recipe_detail', recipe_id=recipe.id))

@app.route("/api/translate/<int:recipe_id>")
def translate_recipe(recipe_id):
    target_lang = request.args.get('lang', session.get('lang', 'en'))
    recipe = Recipe.query.get_or_404(recipe_id)
    
    ingredient_translations = {
        'Mehl (Type 405)': {'de': 'Mehl (Type 405)', 'ru': 'Мука (Тип 405)'},
        'Backkakao': {'de': 'Backkakao', 'ru': 'Какао-порошок'},
        'Zucker': {'de': 'Zucker', 'ru': 'Сахар'},
        'Speiseöl': {'de': 'Speiseöl', 'ru': 'Растительное масло'},
        'Backpulver': {'de': 'Backpulver', 'ru': 'Разрыхлитель'},
        'Wasser': {'de': 'Wasser', 'ru': 'Вода'},
        'Vanilleextrakt': {'de': 'Vanilleextrakt', 'ru': 'Ванильный экстракт'},
    }
    
    def translate_text(text):
        if target_lang == 'en' or not text:
            return text
        
        result = text
        for orig, trans in ingredient_translations.items():
            if target_lang in trans:
                result = result.replace(orig, trans[target_lang])
        
        if target_lang == 'ru':
            result = result.replace('g', 'г').replace('ml', 'мл').replace('TL', 'ч.л.').replace('EL', 'ст.л.')
        return result
    
    return {
        'title': translate_text(recipe.title),
        'description': translate_text(recipe.description) if recipe.description else None,
        'instructions': translate_text(recipe.instructions) if recipe.instructions else None,
        'ingredients': [
            {'name': translate_text(ri.ingredient.name), 'amount': ri.amount, 'unit': ri.unit.name if ri.unit else 'g'}
            for ri in recipe.recipe_ingredients
        ],
        'steps': [{'number': s.step_number, 'instruction': translate_text(s.instruction)} for s in recipe.steps]
    }

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        g_unit = Unit.query.filter_by(name='g').first()
        if not g_unit:
            g_unit = Unit(name='g', unit_type='mass', grams_conversion=1.0, creator_id=1)
            db.session.add(g_unit)
        
        ml_unit = Unit.query.filter_by(name='mL').first()
        if not ml_unit:
            ml_unit = Unit(name='mL', unit_type='volume', grams_conversion=1.0, creator_id=1)
            db.session.add(ml_unit)
        
        tbsp_unit = Unit.query.filter_by(name='EL').first()
        if not tbsp_unit:
            tbsp_unit = Unit(name='EL', unit_type='volume', grams_conversion=15.0, creator_id=1)
            db.session.add(tbsp_unit)
        
        tsp_unit = Unit.query.filter_by(name='TL').first()
        if not tsp_unit:
            tsp_unit = Unit(name='TL', unit_type='volume', grams_conversion=5.0, creator_id=1)
            db.session.add(tsp_unit)
        
        db.session.commit()
        
        user = User.query.first()
        if not user:
            user = User(username='demo', password=bcrypt.generate_password_hash('demo').decode('utf-8'))
            db.session.add(user)
            db.session.commit()
        
        if not Ingredient.query.filter_by(name='Mehl (Type 405)').first():
            mehl = Ingredient(
                name='Mehl (Type 405)',
                density=0.55,
                density_unit='g/ml',
                creator_id=user.id
            )
            db.session.add(mehl)
            db.session.flush()
            
            kakao = Ingredient(
                name='Backkakao',
                density=0.4,
                density_unit='g/ml',
                creator_id=user.id
            )
            db.session.add(kakao)
            db.session.flush()
            
            zucker = Ingredient(
                name='Zucker',
                density=0.85,
                density_unit='g/ml',
                creator_id=user.id
            )
            db.session.add(zucker)
            db.session.flush()
            
            oel = Ingredient(
                name='Speiseöl',
                density=0.92,
                density_unit='g/ml',
                creator_id=user.id
            )
            db.session.add(oel)
            db.session.commit()
        
        if not Recipe.query.filter_by(title='Schokoladenkuchen').first():
            mehl = Ingredient.query.filter_by(name='Mehl (Type 405)').first()
            kakao = Ingredient.query.filter_by(name='Backkakao').first()
            zucker = Ingredient.query.filter_by(name='Zucker').first()
            oel = Ingredient.query.filter_by(name='Speiseöl').first()
            
            recipe = Recipe(
                title='Schokoladenkuchen',
                description='Ein saftiger Schokoladenkuchen mit Kakaopulver, gebacken in einer Springform (Ø 20 cm).',
                instructions='Kuchen nach dem vollständigen Auskühlen nach Belieben mit Puderzucker bestreuen.',
                is_draft=False,
                portions=4,
                creator_id=user.id
            )
            db.session.add(recipe)
            db.session.flush()
            
            ri1 = RecipeIngredient(recipe_id=recipe.id, ingredient_id=mehl.id, amount=250, unit_id=g_unit.id, step_number=1)
            db.session.add(ri1)
            
            ri2 = RecipeIngredient(recipe_id=recipe.id, ingredient_id=kakao.id, amount=3, unit_id=tbsp_unit.id, step_number=1)
            db.session.add(ri2)
            
            bp = Ingredient(
                name='Backpulver',
                density=0.9,
                density_unit='g/ml',
                creator_id=user.id
            )
            db.session.add(bp)
            db.session.flush()
            
            ri3 = RecipeIngredient(recipe_id=recipe.id, ingredient_id=bp.id, amount=2.5, unit_id=tsp_unit.id, step_number=1)
            db.session.add(ri3)
            
            ri4 = RecipeIngredient(recipe_id=recipe.id, ingredient_id=zucker.id, amount=180, unit_id=g_unit.id, step_number=2)
            db.session.add(ri4)
            
            ri5 = RecipeIngredient(recipe_id=recipe.id, ingredient_id=oel.id, amount=100, unit_id=ml_unit.id, step_number=2)
            db.session.add(ri5)
            
            wasser = Ingredient(
                name='Wasser',
                density=1.0,
                density_unit='g/ml',
                creator_id=user.id
            )
            db.session.add(wasser)
            db.session.flush()
            
            ri6 = RecipeIngredient(recipe_id=recipe.id, ingredient_id=wasser.id, amount=250, unit_id=ml_unit.id, step_number=2)
            db.session.add(ri6)
            
            vanille = Ingredient(
                name='Vanilleextrakt',
                density=1.06,
                density_unit='g/ml',
                creator_id=user.id
            )
            db.session.add(vanille)
            db.session.flush()
            
            ri7 = RecipeIngredient(recipe_id=recipe.id, ingredient_id=vanille.id, amount=1, unit_id=tsp_unit.id, step_number=2)
            db.session.add(ri7)
            
            step1 = RecipeStep(recipe_id=recipe.id, step_number=1, instruction='Ofen auf 180 Grad Ober-/Unterhitze (Umluft: 160 Grad) vorheizen. Springform (Ø 20 cm) mit etwas Öl einfetten. Mehl mit Backpulver und Kakaopulver vermischen.')
            db.session.add(step1)
            
            step2 = RecipeStep(recipe_id=recipe.id, step_number=2, instruction='Mischung mit den restlichen Zutaten zusammengeben und alles gut miteinander verrühren. Teig in die Springform geben. Kuchen im vorgeheizten Ofen ca. 35 Min. backen. Mit einem Holzstäbchen prüfen, ob der Kuchen durchgebacken ist. Kuchen vollständig auskühlen lassen.')
            db.session.add(step2)
            
            recipe.languages = 'en,de,ru'
            db.session.flush()
            
            trans_en = RecipeTranslation(
                recipe_id=recipe.id,
                language='en',
                title='Chocolate Cake',
                description='A moist chocolate cake with cocoa powder, baked in a springform pan (Ø 20 cm).',
                instructions='Dust the cake with powdered sugar after it has cooled completely.'
            )
            db.session.add(trans_en)
            
            step1_en = RecipeStepTranslation(
                recipe_id=recipe.id,
                step_number=1,
                language='en',
                instruction='Preheat oven to 180°C (350°F) top/bottom heat (convection: 160°C/320°F). Grease a springform pan (Ø 20 cm) with a little oil. Mix flour with baking powder and cocoa powder.'
            )
            db.session.add(step1_en)
            
            step2_en = RecipeStepTranslation(
                recipe_id=recipe.id,
                step_number=2,
                language='en',
                instruction='Combine with the remaining ingredients and mix well. Pour batter into the springform pan. Bake in preheated oven for about 35 minutes. Test with a wooden skewer to see if done. Let cool completely.'
            )
            db.session.add(step2_en)
            
            trans_ru = RecipeTranslation(
                recipe_id=recipe.id,
                language='ru',
                title='Шоколадный торт',
                description='Влажный шоколадный торт с какао-порошком, испечённый в форме (Ø 20 см).',
                instructions='Посыпьте торт сахарной пудрой после полного остывания.'
            )
            db.session.add(trans_ru)
            
            step1_ru = RecipeStepTranslation(
                recipe_id=recipe.id,
                step_number=1,
                language='ru',
                instruction='Разогрейте духовку до 180°C (конвекция: 160°C). Смажьте форму маслом. Смешайте муку с разрыхлителем и какао-порошком.'
            )
            db.session.add(step1_ru)
            
            step2_ru = RecipeStepTranslation(
                recipe_id=recipe.id,
                step_number=2,
                language='ru',
                instruction='Смешайте с остальными ингредиентами и хорошо перемешайте. Вылейте тесто в форму. Выпекайте в духовке около 35 минут. Проверьте деревянной шпажкой. Дать полностью остыть.'
            )
            db.session.add(step2_ru)
            
            db.session.commit()
        
    app.run(debug=os.environ.get('FLASK_ENV') != 'production', port=8001)
