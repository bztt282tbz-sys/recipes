from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash, abort, session, jsonify
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import uuid
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
    referer = request.headers.get('Referer', '')
    if referer:
        import re
        clean = re.sub(r'\?lang=[a-z]+', '', referer)
        clean = re.sub(r'&lang=[a-z]+', '', clean)
        return redirect(clean if clean else url_for('home'))
    return redirect(url_for('home'))

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

def get_user_grouped_recipes(user_id, exclude_recipe_id=None):
    from sqlalchemy import func
    subquery = db.session.query(
        Recipe.group_id,
        func.max(Recipe.id).label('max_id')
    ).filter(
        Recipe.creator_id == user_id,
        Recipe.group_id != None
    )
    if exclude_recipe_id:
        subquery = subquery.filter(Recipe.id != exclude_recipe_id)
    subquery = subquery.group_by(Recipe.group_id).subquery()
    
    recipes = Recipe.query.join(subquery, Recipe.id == subquery.c.max_id).all()
    
    grouped_languages = {}
    for r in recipes:
        variants = Recipe.query.filter(
            Recipe.group_id == r.group_id,
            Recipe.creator_id == user_id
        ).all()
        if exclude_recipe_id:
            variants = [v for v in variants if v.id != exclude_recipe_id]
        grouped_languages[r.group_id] = [v.language for v in variants]
    return recipes, grouped_languages

def get_group_variants(group_id, creator_id, exclude_recipe_id=None):
    query = Recipe.query.filter(
        Recipe.group_id == group_id,
        Recipe.creator_id == creator_id
    )
    if exclude_recipe_id:
        query = query.filter(Recipe.id != exclude_recipe_id)
    return query.all()

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
            flash('Admin access required.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def convert_to_grams(amount, unit_obj, ingredient):
    if not unit_obj:
        return amount
    if unit_obj.unit_type == 'mass':
        return amount * unit_obj.grams_conversion
    elif unit_obj.unit_type == 'volume':
        density = ingredient.density if ingredient.density else 1.0
        return amount * unit_obj.grams_conversion * density
    return amount

def get_all_ingredients():
    current_lang = session.get('lang', 'en')
    return [{'id': i.id, 'name': i.get_name(current_lang), 'preferred_unit_id': i.preferred_unit_id} for i in Ingredient.query.all()]

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_paused = db.Column(db.Boolean, default=False)
    recipes = db.relationship('Recipe', backref='author', lazy=True)
    ingredients = db.relationship('Ingredient', backref='author', lazy=True)
    units = db.relationship('Unit', backref='author', lazy=True)

class Unit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), nullable=False)
    name_ru = db.Column(db.String(30), nullable=True)
    unit_type = db.Column(db.String(10), nullable=False)
    grams_conversion = db.Column(db.Float, nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_bound = db.Column(db.Boolean, default=False)
    ingredient_units = db.relationship('UnitIngredient', backref='unit', cascade="all, delete-orphan", lazy=True)

    def get_name(self, target_lang=None):
        if target_lang is None:
            target_lang = session.get('lang', 'en')
        if target_lang == 'ru' and self.name_ru:
            return self.name_ru
        return self.name

class Ingredient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    language = db.Column(db.String(10), default='en')
    density = db.Column(db.Float, nullable=True)
    density_unit = db.Column(db.String(10), default='g/ml')
    grams_per_unit = db.Column(db.Float, nullable=True)
    unit_name = db.Column(db.String(30), nullable=True)
    preferred_unit_id = db.Column(db.Integer, db.ForeignKey('unit.id'), nullable=True)
    preferred_unit = db.relationship('Unit', foreign_keys=[preferred_unit_id])
    comment = db.Column(db.String(100), nullable=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    unit_associations = db.relationship('UnitIngredient', backref='ingredient', cascade="all, delete-orphan", lazy=True)

    def get_name(self, target_lang=None):
        if target_lang is None:
            target_lang = session.get('lang', 'en')
        if target_lang == 'en':
            return self.name
        trans = IngredientTranslation.query.filter_by(ingredient_id=self.id, language=target_lang).first()
        if trans:
            return trans.name
        if self.language == target_lang:
            return self.name
        fallback_order = ['en', 'de', 'ru']
        for lang in fallback_order:
            if lang != target_lang:
                if self.language == lang:
                    return self.name
                trans = IngredientTranslation.query.filter_by(ingredient_id=self.id, language=lang).first()
                if trans:
                    return trans.name
        return self.name

    def get_available_units(self):
        units = Unit.query.all()
        available = []
        for unit in units:
            if unit.is_bound:
                for ui in unit.ingredient_units:
                    if ui.ingredient_id == self.id:
                        available.append({'id': unit.id, 'name': unit.name, 'unit_type': unit.unit_type, 'grams_conversion': unit.grams_conversion, 'is_bound': True})
                        break
            else:
                available.append({'id': unit.id, 'name': unit.name, 'unit_type': unit.unit_type, 'grams_conversion': unit.grams_conversion, 'is_bound': False})
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
    language = db.Column(db.String(10), default='en')
    is_draft = db.Column(db.Boolean, default=True)
    portions = db.Column(db.Float, default=1)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    group_id = db.Column(db.String(36), nullable=True, index=True)
    recipe_ingredients = db.relationship('RecipeIngredient', backref='recipe', cascade="all, delete-orphan", lazy=True)
    steps = db.relationship('RecipeStep', backref='recipe', cascade="all, delete-orphan", lazy=True, order_by='RecipeStep.step_number')
    __table_args__ = (db.Index('idx_group_creator', 'group_id', 'creator_id'),)

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
                    'original_unit_id': ri.unit_id,
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
        target_lang = session.get('lang', 'en')
        if self.unit:
            if target_lang == 'ru' and self.unit.name_ru:
                return self.unit.name_ru
            return self.unit.name
        return 'г' if target_lang == 'ru' else 'g'

class RecipeStep(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey('recipe.id'), nullable=False)
    step_number = db.Column(db.Integer, nullable=False)
    instruction = db.Column(db.Text, nullable=False)

class IngredientTranslation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('ingredient.id'), nullable=False)
    language = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    __table_args__ = (db.UniqueConstraint('ingredient_id', 'language'),)
    ingredient = db.relationship('Ingredient', backref='translations')

@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except (ValueError, TypeError):
        return None

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
    if 'lang' not in session:
        session['lang'] = 'en'

@app.context_processor
def inject_translations():
    translations_dict = {
        'en': {
            'home': 'Home', 'add_recipe': 'Add Recipe',
            'add_ingredient': 'Add Ingredient', 'add_unit': 'Add Unit', 'login': 'Login',
            'register': 'Register', 'logout': 'Logout', 'search': 'Search', 'all_recipes': 'All Recipes',
            'download': 'Download Recipe', 'manage_data': 'Manage Data',
            'ingredients': 'Ingredients', 'instructions': 'Instructions', 'notes': 'Notes', 'portions': 'Portions',
            'actions': 'Actions', 'tips': 'Tips', 'export': 'Export', 'copy_list': 'Copy List',
            'discover_recipes': 'Discover Delicious Recipes', 'find_share': 'Find and share your favorite culinary creations',
            'search_recipes': 'Search recipes...', 'search_results': 'Search Results', 'no_recipes': 'No recipes found',
            'try_different': 'Try a different search term', 'create_first': 'Start by creating your first recipe!',
            'view_recipe': 'View Recipe', 'edit_recipe': 'Edit Recipe', 'draft': 'Draft', 'no_description': 'No description', 'grouped': 'grouped',
            'step': 'Step', 'ingredients_for_step': 'Ingredients for this step', 'convert_to': 'Convert to', 'original': 'original',
            'close': 'Close', 'save': 'Save', 'cancel': 'Cancel',
            'delete': 'Delete', 'confirm_delete': 'Confirm Delete',
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
            'print_recipe': 'Print Recipe', 'english': 'English', 'german': 'German', 'russian': 'Russian',
            'settings': 'Settings',             'admin_panel': 'Admin Panel', 'users': 'Users', 'promote': 'Promote to Admin',
            'demote': 'Demote from Admin', 'pause_user': 'Pause User', 'activate_user': 'Activate User',
            'delete_user': 'Delete User', 'user_deleted': 'User deleted.',
            'confirm_delete_user': 'Delete this user and ALL their data (recipes, ingredients, units)? This cannot be undone.',
            'delete_recipe': 'Delete Recipe', 'recipe_deleted': 'Recipe deleted.',
            'delete_ingredient': 'Delete Ingredient', 'ingredient_deleted': 'Ingredient deleted.',
            'ingredient_in_use': 'Cannot delete ingredient "%s" — it is used in %d recipe(s).',
            'delete_unit': 'Delete Unit', 'unit_deleted': 'Unit deleted.',
            'unit_in_use': 'Cannot delete unit "%s" — it is used in %d recipe(s).',
            'change_name': 'Change Name', 'change_password': 'Change Password', 'current_password': 'Current Password',
            'new_password': 'New Password', 'confirm_password': 'Confirm Password', 'name_updated': 'Name updated!',
            'password_updated': 'Password updated!', 'wrong_password': 'Current password is incorrect.',
            'passwords_mismatch': 'Passwords do not match.', 'is_admin': 'Admin', 'is_paused': 'Paused',
            'active': 'Active', 'change_name_title': 'Change Username', 'change_password_title': 'Change Password',
            'current_password_required': 'Current password is required to make changes.',
            'add_demo_recipe': 'Add Demo Recipe', 'demo_recipe_exists': 'Demo Recipe Exists',
            'skip': 'Skip', 'take_tour': 'Take the Tour', 'next': 'Next', 'choose_language': 'Choose your language:',
            'tour_done': "You're all set!", 'tour_done_subtitle': 'Start exploring, add your first recipe, or browse what others have shared.',
            'get_started': 'Get Started',
            'tour_olive_oil': 'Olive Oil', 'tour_flour': 'Flour', 'tour_sugar': 'Sugar', 'tour_eggs': 'Eggs',
            'tour_chocolate_cake': 'Chocolate Cake', 'tour_butter': 'Butter', 'tour_milk': 'Milk',
            'tour_step1': 'Mix ingredients', 'tour_step2': 'Add wet ingredients', 'tour_step3': 'Brush top with milk',
            'g_abbr': 'g', 'ml_abbr': 'ml', 'cups_abbr': 'cups',
            'tour_convert_result': '91.2 g', 'tour_custom_result': '0.88 cups',
            'about_features': 'Features',
            'about_unit_conversion_heading': 'Unit Conversion',
            'about_unit_conversion': 'Click any ingredient amount on a recipe page to instantly convert between units — grams, cups, mL, tablespoons, and more. No more googling "how many grams in 100 ml of oil".',
            'about_portion_scaling_heading': 'Portion Scaling',
            'about_portion_scaling': 'Scale any recipe up or down with the +/- buttons. All ingredient amounts adjust automatically.',
            'about_multilang_recipes_heading': 'Multi-Language Recipes',
            'about_multilang_recipes': 'Link translations of the same dish together. View a recipe in English, then switch to German or Russian with one click.',
            'about_multilang_ui_heading': 'Multi-Language UI',
            'about_multilang_ui': 'The entire interface is available in English, German, and Russian. Switch languages from the navbar.',
            'about_step_by_step_heading': 'Step-by-Step Instructions',
            'about_step_by_step': 'Each cooking step has its own set of ingredients, so you always know what goes in when.',
            'about_download_md_heading': 'Download as Markdown',
            'about_download_md': 'Export any recipe as a .md file to save, share, or print.',
            'about_custom_units_heading': 'Custom Ingredients & Units',
            'about_custom_units': 'Create your own ingredients with density-aware conversion factors. Define custom measurement units and bind them to specific ingredients.',
            'about_drafts_search_heading': 'Drafts & Search',
            'about_drafts_search': 'Save recipes as drafts while you work on them. Find any recipe instantly with full-text search.',
            'onboard_welcome': 'Welcome to Flavor Archive',
            'onboard_subtitle': 'No recipes yet \u2014 here\u2019s what you can do:',
            'onboard_click_convert_heading': 'Click to Convert',
            'onboard_click_convert': 'Click any ingredient amount on a recipe to instantly switch between grams, cups, mL, and more. No math, no googling.',
            'onboard_scale_portions_heading': 'Scale Portions',
            'onboard_scale_portions': 'Use the +/- buttons to scale any recipe up or down. All ingredient amounts update automatically.',
            'onboard_multilang_heading': 'Multi-Language',
            'onboard_multilang': 'Link recipe translations together and switch between English, German, or Russian with one click.',
            'onboard_step_by_step_heading': 'Step-by-Step',
            'onboard_step_by_step': 'Each cooking step has its own ingredients, so you always know what goes in when.',
            'onboard_custom_units_heading': 'Custom Units',
            'onboard_custom_units': 'Create your own ingredients and units with density-aware conversion. Perfect for non-standard measures.',
            'onboard_export_heading': 'Export & Share',
            'onboard_export': 'Download any recipe as Markdown to save, share, or print whenever you need.',
            'about': 'About',
            'impressum': 'Impressum',
            'privacy': 'Datenschutzhinweise',
        },
        'de': {
            'home': 'Startseite', 'add_recipe': 'Rezept hinzufügen',
            'add_ingredient': 'Zutat hinzufügen', 'add_unit': 'Einheit hinzufügen', 'login': 'Anmelden',
            'register': 'Registrieren', 'logout': 'Abmelden', 'search': 'Suchen', 'all_recipes': 'Alle Rezepte',
            'download': 'Rezept herunterladen', 'manage_data': 'Daten verwalten',
            'ingredients': 'Zutaten', 'instructions': 'Anleitung', 'notes': 'Notizen', 'portions': 'Portionen',
            'actions': 'Aktionen', 'tips': 'Tipps', 'export': 'Exportieren', 'copy_list': 'Liste kopieren',
            'discover_recipes': 'Entdecken Sie köstliche Rezepte', 'find_share': 'Teilen Sie Ihre Lieblingsgerichte',
            'search_recipes': 'Rezepte suchen...', 'search_results': 'Suchergebnisse', 'no_recipes': 'Keine Rezepte gefunden',
            'try_different': 'Versuchen Sie einen anderen Suchbegriff', 'create_first': 'Erstellen Sie Ihr erstes Rezept!',
            'view_recipe': 'Rezept ansehen', 'edit_recipe': 'Rezept bearbeiten', 'draft': 'Entwurf', 'no_description': 'Keine Beschreibung', 'grouped': 'gruppiert',
            'step': 'Schritt', 'ingredients_for_step': 'Zutaten für diesen Schritt', 'convert_to': 'Umrechnen zu', 'original': 'Original',
            'close': 'Schließen', 'save': 'Speichern', 'cancel': 'Abbrechen',
            'delete': 'Löschen', 'confirm_delete': 'Löschen bestätigen',
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
            'print_recipe': 'Rezept drucken', 'english': 'Englisch', 'german': 'Deutsch', 'russian': 'Russisch',
            'settings': 'Einstellungen',             'admin_panel': 'Admin-Panel', 'users': 'Benutzer', 'promote': 'Zum Admin machen',
            'demote': 'Admin-Status entfernen', 'pause_user': 'Benutzer pausieren', 'activate_user': 'Benutzer aktivieren',
            'delete_user': 'Benutzer löschen', 'user_deleted': 'Benutzer gelöscht.',
            'confirm_delete_user': 'Diesen Benutzer und ALLE seine Daten (Rezepte, Zutaten, Einheiten) löschen? Dies kann nicht rückgängig gemacht werden.',
            'delete_recipe': 'Rezept löschen', 'recipe_deleted': 'Rezept gelöscht.',
            'delete_ingredient': 'Zutat löschen', 'ingredient_deleted': 'Zutat gelöscht.',
            'ingredient_in_use': 'Zutat "%s" kann nicht gelöscht werden — sie wird in %d Rezept(en) verwendet.',
            'delete_unit': 'Einheit löschen', 'unit_deleted': 'Einheit gelöscht.',
            'unit_in_use': 'Einheit "%s" kann nicht gelöscht werden — sie wird in %d Rezept(en) verwendet.',
            'change_name': 'Name ändern', 'change_password': 'Passwort ändern', 'current_password': 'Aktuelles Passwort',
            'new_password': 'Neues Passwort', 'confirm_password': 'Passwort bestätigen', 'name_updated': 'Name aktualisiert!',
            'password_updated': 'Passwort aktualisiert!', 'wrong_password': 'Aktuelles Passwort ist falsch.',
            'passwords_mismatch': 'Passwörter stimmen nicht überein.', 'is_admin': 'Admin', 'is_paused': 'Pausiert',
            'active': 'Aktiv', 'change_name_title': 'Benutzername ändern', 'change_password_title': 'Passwort ändern',
            'current_password_required': 'Aktuelles Passwort ist erforderlich.',
            'add_demo_recipe': 'Demo-Rezept hinzufügen', 'demo_recipe_exists': 'Demo-Rezept existiert',
            'skip': 'Überspringen', 'take_tour': 'Tour starten', 'next': 'Weiter', 'choose_language': 'Wählen Sie Ihre Sprache:',
            'tour_done': 'Bereit!', 'tour_done_subtitle': 'Erkunden Sie die App, erstellen Sie Ihr erstes Rezept oder stöbern Sie in den Rezepten anderer.',
            'get_started': 'Loslegen',
            'tour_olive_oil': 'Olivenöl', 'tour_flour': 'Mehl', 'tour_sugar': 'Zucker', 'tour_eggs': 'Eier',
            'tour_chocolate_cake': 'Schokoladenkuchen', 'tour_butter': 'Butter', 'tour_milk': 'Milch',
            'tour_step1': 'Zutaten mischen', 'tour_step2': 'Feuchte Zutaten hinzufügen', 'tour_step3': 'Mit Milch bestreichen',
            'g_abbr': 'g', 'ml_abbr': 'ml', 'cups_abbr': 'Tassen',
            'tour_convert_result': '91.2 g', 'tour_custom_result': '0.88 Tassen',
            'about_features': 'Funktionen',
            'about_unit_conversion_heading': 'Einheitenumrechnung',
            'about_unit_conversion': 'Klicken Sie auf eine beliebige Zutatenmenge auf einer Rezeptseite, um sofort zwischen Einheiten umzurechnen — Gramm, Tassen, ml, Esslöffel und mehr. Kein Googeln mehr nach "wie viel Gramm sind 100 ml Öl".',
            'about_portion_scaling_heading': 'Portionsanpassung',
            'about_portion_scaling': 'Passen Sie jedes Rezept mit den +/- Tasten an. Alle Zutatenmengen werden automatisch aktualisiert.',
            'about_multilang_recipes_heading': 'Mehrsprachige Rezepte',
            'about_multilang_recipes': 'Verknüpfen Sie Übersetzungen desselben Gerichts. Betrachten Sie ein Rezept auf Englisch und wechseln Sie mit einem Klick zu Deutsch oder Russisch.',
            'about_multilang_ui_heading': 'Mehrsprachige Oberfläche',
            'about_multilang_ui': 'Die gesamte Oberfläche ist auf Englisch, Deutsch und Russisch verfügbar. Wechseln Sie die Sprache über die Navigationsleiste.',
            'about_step_by_step_heading': 'Schritt-für-Schritt-Anleitung',
            'about_step_by_step': 'Jeder Kochschritt hat seine eigenen Zutaten, sodass Sie immer wissen, was wann hineinkommt.',
            'about_download_md_heading': 'Als Markdown herunterladen',
            'about_download_md': 'Exportieren Sie jedes Rezept als .md-Datei zum Speichern, Teilen oder Ausdrucken.',
            'about_custom_units_heading': 'Benutzerdefinierte Zutaten & Einheiten',
            'about_custom_units': 'Erstellen Sie eigene Zutaten mit dichteabhängigen Umrechnungsfaktoren. Definieren Sie benutzerdefinierte Maßeinheiten und binden Sie sie an bestimmte Zutaten.',
            'about_drafts_search_heading': 'Entwürfe & Suche',
            'about_drafts_search': 'Speichern Sie Rezepte als Entwürfe, während Sie daran arbeiten. Finden Sie jedes Rezept sofort mit der Volltextsuche.',
            'onboard_welcome': 'Willkommen bei Flavor Archive',
            'onboard_subtitle': 'Noch keine Rezepte \u2014 hier erfahren Sie, was Sie tun können:',
            'onboard_click_convert_heading': 'Klicken & Umrechnen',
            'onboard_click_convert': 'Klicken Sie auf eine Zutatenmenge in einem Rezept, um sofort zwischen Gramm, Tassen, ml und mehr zu wechseln. Kein Rechnen, kein Googeln.',
            'onboard_scale_portions_heading': 'Portionen anpassen',
            'onboard_scale_portions': 'Nutzen Sie die +/- Tasten, um jedes Rezept zu vergrößern oder zu verkleinern. Alle Zutatenmengen passen sich automatisch an.',
            'onboard_multilang_heading': 'Mehrsprachig',
            'onboard_multilang': 'Verknüpfen Sie Rezeptübersetzungen und wechseln Sie mit einem Klick zwischen Englisch, Deutsch oder Russisch.',
            'onboard_step_by_step_heading': 'Schritt für Schritt',
            'onboard_step_by_step': 'Jeder Kochschritt hat seine eigenen Zutaten, damit Sie immer wissen, was wann hineinkommt.',
            'onboard_custom_units_heading': 'Eigene Einheiten',
            'onboard_custom_units': 'Erstellen Sie eigene Zutaten und Einheiten mit dichteabhängiger Umrechnung. Perfekt für nicht standardisierte Maße.',
            'onboard_export_heading': 'Exportieren & Teilen',
            'onboard_export': 'Laden Sie jedes Rezept als Markdown herunter, um es zu speichern, zu teilen oder bei Bedarf auszudrucken.',
            'about': 'Über',
            'impressum': 'Impressum',
            'privacy': 'Datenschutzhinweise',
        },
        'ru': {
            'home': 'Главная', 'add_recipe': 'Добавить рецепт',
            'add_ingredient': 'Добавить ингредиент', 'add_unit': 'Добавить единицу', 'login': 'Войти',
            'register': 'Регистрация', 'logout': 'Выйти', 'search': 'Поиск', 'all_recipes': 'Все рецепты',
            'download': 'Скачать рецепт', 'manage_data': 'Управление данными',
            'ingredients': 'Ингредиенты', 'instructions': 'Инструкции', 'notes': 'Заметки', 'portions': 'Порции',
            'actions': 'Действия', 'tips': 'Советы', 'export': 'Экспорт', 'copy_list': 'Копировать список',
            'discover_recipes': 'Откройте для себя вкусные рецепты', 'find_share': 'Делитесь своими любимыми блюдами',
            'search_recipes': 'Поиск рецептов...', 'search_results': 'Результаты поиска', 'no_recipes': 'Рецепты не найдены',
            'try_different': 'Попробуйте другой поисковый запрос', 'create_first': 'Создайте свой первый рецепт!',
            'view_recipe': 'Посмотреть рецепт', 'edit_recipe': 'Редактировать рецепт', 'draft': 'Черновик', 'no_description': 'Нет описания', 'grouped': 'группированный',
            'step': 'Шаг', 'ingredients_for_step': 'Ингредиенты для этого шага', 'convert_to': 'Конвертировать в', 'original': 'оригинал',
            'close': 'Закрыть', 'save': 'Сохранить', 'cancel': 'Отмена',
            'delete': 'Удалить', 'confirm_delete': 'Подтвердить удаление',
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
            'grams_per_unit': 'Грамм на единицу', 'unit_name': 'Название единицы', 'preferred_unit': 'Предпочитаемая единица',
            'comment': 'Комментарий', 'unit_type': 'Тип единицы', 'mass': 'Масса', 'volume': 'Объём', 'count': 'Штука',
            'grams_conversion': 'Конвертация в граммы', 'bound_to_ingredient': 'Связать с ингредиентом',
            'available_units': 'Доступные единицы', 'add_item': 'Добавить', 'quantity': 'Количество',
            'best_before': 'Годен до', 'shopping_list_title': 'Список покупок',
            'copy_to_clipboard': 'Копировать в буфер',
            'recipe_created': 'Рецепт создан!', 'recipe_updated': 'Рецепт обновлён!',
            'print_recipe': 'Печать рецепта', 'english': 'Английский', 'german': 'Немецкий', 'russian': 'Русский',
            'settings': 'Настройки',             'admin_panel': 'Админ-панель', 'users': 'Пользователи', 'promote': 'Сделать админом',
            'demote': 'Убрать статус админа', 'pause_user': 'Приостановить пользователя', 'activate_user': 'Активировать пользователя',
            'delete_user': 'Удалить пользователя', 'user_deleted': 'Пользователь удалён.',
            'confirm_delete_user': 'Удалить этого пользователя и ВСЕ его данные (рецепты, ингредиенты, единицы)? Это действие необратимо.',
            'delete_recipe': 'Удалить рецепт', 'recipe_deleted': 'Рецепт удалён.',
            'delete_ingredient': 'Удалить ингредиент', 'ingredient_deleted': 'Ингредиент удалён.',
            'ingredient_in_use': 'Ингредиент "%s" нельзя удалить — он используется в %d рецепте(ах).',
            'delete_unit': 'Удалить единицу', 'unit_deleted': 'Единица удалена.',
            'unit_in_use': 'Единицу "%s" нельзя удалить — она используется в %d рецепте(ах).',
            'change_name': 'Изменить имя', 'change_password': 'Изменить пароль', 'current_password': 'Текущий пароль',
            'new_password': 'Новый пароль', 'confirm_password': 'Подтвердите пароль', 'name_updated': 'Имя обновлено!',
            'password_updated': 'Пароль обновлён!', 'wrong_password': 'Текущий пароль неверный.',
            'passwords_mismatch': 'Пароли не совпадают.', 'is_admin': 'Админ', 'is_paused': 'Приостановлен',
            'active': 'Активен', 'change_name_title': 'Изменить имя пользователя', 'change_password_title': 'Изменить пароль',
            'current_password_required': 'Текущий пароль обязателен.',
            'add_demo_recipe': 'Добавить демо-рецепт', 'demo_recipe_exists': 'Демо-рецепт существует',
            'skip': 'Пропустить', 'take_tour': 'Начать тур', 'next': 'Далее', 'choose_language': 'Выберите язык:',
            'tour_done': 'Всё готово!', 'tour_done_subtitle': 'Начинайте исследовать, добавьте первый рецепт или посмотрите, что создали другие.',
            'get_started': 'Начать',
            'tour_olive_oil': 'Оливковое масло', 'tour_flour': 'Мука', 'tour_sugar': 'Сахар', 'tour_eggs': 'Яйца',
            'tour_chocolate_cake': 'Шоколадный торт', 'tour_butter': 'Масло', 'tour_milk': 'Молоко',
            'tour_step1': 'Смешайте ингредиенты', 'tour_step2': 'Добавьте влажные ингредиенты', 'tour_step3': 'Смазать верх молоком',
            'g_abbr': 'г', 'ml_abbr': 'мл', 'cups_abbr': 'чашки',
            'tour_convert_result': '91.2 г', 'tour_custom_result': '0.88 чашки',
            'about_features': 'Возможности',
            'about_unit_conversion_heading': 'Конвертация единиц',
            'about_unit_conversion': 'Нажмите на любое количество ингредиента на странице рецепта, чтобы мгновенно переключиться между граммами, чашками, мл, столовыми ложками и другими единицами. Больше не нужно гуглить "сколько грамм в 100 мл масла".',
            'about_portion_scaling_heading': 'Масштабирование порций',
            'about_portion_scaling': 'Масштабируйте любой рецепт с помощью кнопок +/-. Все количества ингредиентов обновляются автоматически.',
            'about_multilang_recipes_heading': 'Многоязычные рецепты',
            'about_multilang_recipes': 'Связывайте переводы одного и того же блюда вместе. Просматривайте рецепт на английском, затем переключайтесь на немецкий или русский одним кликом.',
            'about_multilang_ui_heading': 'Многоязычный интерфейс',
            'about_multilang_ui': 'Весь интерфейс доступен на английском, немецком и русском языках. Переключайте язык в панели навигации.',
            'about_step_by_step_heading': 'Пошаговые инструкции',
            'about_step_by_step': 'Каждый этап приготовления имеет свой набор ингредиентов, поэтому вы всегда знаете, что и когда добавлять.',
            'about_download_md_heading': 'Скачать как Markdown',
            'about_download_md': 'Экспортируйте любой рецепт в файл .md для сохранения, отправки или печати.',
            'about_custom_units_heading': 'Свои ингредиенты и единицы',
            'about_custom_units': 'Создавайте собственные ингредиенты с коэффициентами конвертации, учитывающими плотность. Определяйте пользовательские единицы измерения и привязывайте их к конкретным ингредиентам.',
            'about_drafts_search_heading': 'Черновики и поиск',
            'about_drafts_search': 'Сохраняйте рецепты как черновики во время работы. Находите любой рецепт мгновенно с помощью полнотекстового поиска.',
            'onboard_welcome': 'Добро пожаловать в Flavor Archive',
            'onboard_subtitle': 'Рецептов пока нет — вот что вы можете сделать:',
            'onboard_click_convert_heading': 'Нажми для конвертации',
            'onboard_click_convert': 'Нажмите на количество ингредиента в рецепте, чтобы мгновенно переключиться между граммами, чашками, мл и другими единицами. Никакой математики, никакого гугления.',
            'onboard_scale_portions_heading': 'Масштаб порций',
            'onboard_scale_portions': 'Используйте кнопки +/-, чтобы увеличить или уменьшить любой рецепт. Все количества ингредиентов обновляются автоматически.',
            'onboard_multilang_heading': 'Несколько языков',
            'onboard_multilang': 'Связывайте переводы рецептов и переключайтесь между английским, немецким или русским одним кликом.',
            'onboard_step_by_step_heading': 'По шагам',
            'onboard_step_by_step': 'Каждый этап приготовления имеет свои ингредиенты, чтобы вы всегда знали, что и когда добавлять.',
            'onboard_custom_units_heading': 'Свои единицы',
            'onboard_custom_units': 'Создавайте собственные ингредиенты и единицы с конвертацией на основе плотности. Идеально для нестандартных мер.',
            'onboard_export_heading': 'Экспорт и отправка',
            'onboard_export': 'Скачивайте любой рецепт в формате Markdown, чтобы сохранить, отправить или распечатать когда угодно.',
            'about': 'О проекте',
            'impressum': 'Импрессум',
            'privacy': 'Защита данных',
        }
    }

    current_lang = session.get('lang', 'en')

    def translate_ingredient_name(ing):
        return ing.get_name(current_lang)

    def translate_unit_name(unit):
        if hasattr(unit, 'get_name'):
            return unit.get_name(current_lang)
        if isinstance(unit, dict):
            return unit.get('name_ru', unit.get('name', ''))
        return str(unit)

    def get_unit_name(unit_id):
        if unit_id is None:
            return 'г' if current_lang == 'ru' else 'g'
        try:
            unit = Unit.query.get(int(unit_id))
            if unit:
                return translate_unit_name(unit)
        except (ValueError, TypeError):
            pass
        return 'г' if current_lang == 'ru' else 'g'

    return dict(
        t=lambda key: translations_dict.get(current_lang, translations_dict['en']).get(key, key),
        lang=current_lang,
        languages=[('en', 'EN'), ('de', 'DE'), ('ru', 'RU')],
        translate_ing=translate_ingredient_name,
        translate_unit=translate_unit_name,
        get_unit_name=get_unit_name,
        request=request
    )

@app.route("/about")
def about():
    return render_template('about.html')

@app.route("/impressum")
def impressum():
    return render_template('legal/impressum.html')

@app.route("/privacy")
def privacy():
    return render_template('legal/privacy.html')

@app.route("/")
def home():
    search_query = request.args.get('q', '')
    current_lang = session.get('lang', 'en')

    if current_user.is_authenticated:
        base_filter = (
            (Recipe.is_draft == False) | (Recipe.creator_id == current_user.id)
        )
    else:
        base_filter = Recipe.is_draft == False

    recipes_by_group = {}
    standalone_recipes = []

    all_recipes = Recipe.query.filter(base_filter)
    if search_query:
        all_recipes = all_recipes.filter(Recipe.title.ilike(f'%{search_query}%'))
    all_recipes = all_recipes.order_by(Recipe.id.desc()).all()

    for recipe in all_recipes:
        if recipe.group_id:
            group_key = (recipe.group_id, recipe.creator_id)
            if group_key not in recipes_by_group:
                recipes_by_group[group_key] = []
            recipes_by_group[group_key].append(recipe)
        else:
            standalone_recipes.append(recipe)

    primary_grouped = []
    for group_key, group_recipes in recipes_by_group.items():
        target_recipe = None
        for r in group_recipes:
            if r.language == current_lang:
                target_recipe = r
                break
        if target_recipe is None:
            for lang_pref in ['en', 'de', 'ru']:
                for r in group_recipes:
                    if r.language == lang_pref:
                        target_recipe = r
                        break
                if target_recipe:
                    break
        if target_recipe:
            target_recipe._group_variants = [r for r in group_recipes if r.id != target_recipe.id]
            primary_grouped.append(target_recipe)

    primary_recipes = [r for r in standalone_recipes if r.language == current_lang]
    secondary_recipes = [r for r in standalone_recipes if r.language != current_lang]

    primary_grouped.sort(key=lambda x: x.id, reverse=True)
    primary_recipes.sort(key=lambda x: x.id, reverse=True)
    secondary_recipes.sort(key=lambda x: x.id, reverse=True)

    return render_template('home.html', recipes=primary_recipes + primary_grouped, secondary_recipes=secondary_recipes, search_query=search_query)

@app.route("/recipe/<int:recipe_id>")
def recipe_detail(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    if recipe.is_draft and (not current_user.is_authenticated or recipe.creator_id != current_user.id):
        abort(404)
    return render_template('recipe_detail.html', recipe=recipe)

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
            all_ingredients = get_all_ingredients()
            all_units = [{'id': u.id, 'name': u.name, 'name_ru': u.name_ru or u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
            return render_template('add_recipe.html', all_ingredients=all_ingredients, all_units=all_units)

        portions = 1
        if request.form.get('portions'):
            try:
                portions = float(request.form.get('portions'))
                if portions <= 0:
                    raise ValueError()
            except ValueError:
                flash('Portions must be a positive number', 'danger')
                all_ingredients = get_all_ingredients()
                all_units = [{'id': u.id, 'name': u.name, 'name_ru': u.name_ru or u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
                return render_template('add_recipe.html', all_ingredients=all_ingredients, all_units=all_units)

        language = request.form.get('language', session.get('lang', 'en'))

        new_recipe = Recipe(
            title=title,
            description=description,
            instructions=instructions,
            is_draft=is_draft,
            portions=portions,
            language=language,
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
            all_ingredients = get_all_ingredients()
            all_units = [{'id': u.id, 'name': u.name, 'name_ru': u.name_ru or u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
            return render_template('add_recipe.html', all_ingredients=all_ingredients, all_units=all_units)

    all_ingredients = get_all_ingredients()
    all_units = []
    for u in Unit.query.all():
        unit_data = {
            'id': u.id,
            'name': u.name,
            'name_ru': u.name_ru or u.name,
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
    if recipe.creator_id != current_user.id and not current_user.is_admin:
        flash('You cannot edit this recipe.', 'danger')
        return redirect(url_for('home'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required', 'danger')
            all_ingredients = get_all_ingredients()
            all_units = [{'id': u.id, 'name': u.name, 'name_ru': u.name_ru or u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
            grouped_recipes, grouped_languages = get_user_grouped_recipes(current_user.id, recipe.id)
            grouped_variants = get_group_variants(recipe.group_id, current_user.id, recipe.id) if recipe.group_id else []
            return render_template('edit_recipe.html', recipe=recipe, all_ingredients=all_ingredients, all_units=all_units, grouped_recipes=grouped_recipes, grouped_languages=grouped_languages, grouped_variants=grouped_variants)

        recipe.title = title
        recipe.description = request.form.get('description', '').strip()
        recipe.instructions = request.form.get('instructions', '').strip()
        recipe.is_draft = True if request.form.get('is_draft') else False
        recipe.language = request.form.get('language', recipe.language)

        new_group_id = request.form.get('group_id')
        if new_group_id == '__new_group__':
            recipe.group_id = str(uuid.uuid4())
        elif new_group_id:
            recipe.group_id = new_group_id
        else:
            recipe.group_id = None

        portions = recipe.portions
        if request.form.get('portions'):
            try:
                portions = float(request.form.get('portions'))
                if portions <= 0:
                    raise ValueError()
            except ValueError:
                flash('Portions must be a positive number', 'danger')
                all_ingredients = get_all_ingredients()
                all_units = [{'id': u.id, 'name': u.name, 'name_ru': u.name_ru or u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
                grouped_recipes, grouped_languages = get_user_grouped_recipes(current_user.id, recipe.id)
                grouped_variants = get_group_variants(recipe.group_id, current_user.id, recipe.id) if recipe.group_id else []
                return render_template('edit_recipe.html', recipe=recipe, all_ingredients=all_ingredients, all_units=all_units, grouped_recipes=grouped_recipes, grouped_languages=grouped_languages, grouped_variants=grouped_variants)
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
                    step_number=int(step_num_str) if step_num_str and step_num_str.strip() else None
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
            all_ingredients = get_all_ingredients()
            all_units = [{'id': u.id, 'name': u.name, 'name_ru': u.name_ru or u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
            grouped_recipes, grouped_languages = get_user_grouped_recipes(current_user.id, recipe.id)
            grouped_variants = get_group_variants(recipe.group_id, current_user.id, recipe.id) if recipe.group_id else []
            return render_template('edit_recipe.html', recipe=recipe, all_ingredients=all_ingredients, all_units=all_units, grouped_recipes=grouped_recipes, grouped_languages=grouped_languages, grouped_variants=grouped_variants)

    all_ingredients = get_all_ingredients()
    all_units = [{'id': u.id, 'name': u.name, 'name_ru': u.name_ru or u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
    
    grouped_recipes, grouped_languages = get_user_grouped_recipes(current_user.id, recipe.id)
    grouped_variants = get_group_variants(recipe.group_id, current_user.id, recipe.id) if recipe.group_id else []
    
    return render_template('edit_recipe.html', recipe=recipe, all_ingredients=all_ingredients, all_units=all_units, grouped_recipes=grouped_recipes, grouped_languages=grouped_languages, grouped_variants=grouped_variants)

@app.route("/recipe/<int:recipe_id>/ungroup")
@login_required
def ungroup_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    if recipe.creator_id != current_user.id and not current_user.is_admin:
        flash('You cannot modify this recipe.', 'danger')
        return redirect(url_for('home'))
    recipe.group_id = None
    db.session.commit()
    flash('Recipe removed from group.', 'success')
    return redirect(url_for('edit_recipe', recipe_id=recipe.id))

@app.route("/recipe/<int:recipe_id>/delete", methods=['POST'])
@login_required
def delete_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    if recipe.creator_id != current_user.id and not current_user.is_admin:
        flash('You cannot delete this recipe.', 'danger')
        return redirect(url_for('home'))
    RecipeIngredient.query.filter_by(recipe_id=recipe.id).delete()
    RecipeStep.query.filter_by(recipe_id=recipe.id).delete()
    db.session.delete(recipe)
    db.session.commit()
    flash('Recipe deleted.', 'success')
    return redirect(url_for('home'))

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
            if user.is_paused:
                flash('Account is paused.', 'danger')
                return redirect(url_for('login'))
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

@app.route("/settings", methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'change_name':
            new_username = request.form.get('username', '').strip()
            current_pass = request.form.get('current_password', '')

            if not bcrypt.check_password_hash(current_user.password, current_pass):
                flash('wrong_password', 'danger')
                return redirect(url_for('settings'))

            if len(new_username) < 3 or len(new_username) > 20:
                flash('Username must be 3-20 characters', 'danger')
                return redirect(url_for('settings'))

            if not new_username.isalnum():
                flash('Username must be alphanumeric', 'danger')
                return redirect(url_for('settings'))

            existing = User.query.filter_by(username=new_username).first()
            if existing and existing.id != current_user.id:
                flash('username_taken', 'danger')
                return redirect(url_for('settings'))

            current_user.username = new_username
            db.session.commit()
            flash('name_updated', 'success')
            return redirect(url_for('settings'))

        elif action == 'change_password':
            current_pass = request.form.get('current_password', '')
            new_pass = request.form.get('new_password', '')
            confirm_pass = request.form.get('confirm_password', '')

            if not bcrypt.check_password_hash(current_user.password, current_pass):
                flash('wrong_password', 'danger')
                return redirect(url_for('settings'))

            if len(new_pass) < 8:
                flash('Password must be at least 8 characters', 'danger')
                return redirect(url_for('settings'))

            if new_pass != confirm_pass:
                flash('passwords_mismatch', 'danger')
                return redirect(url_for('settings'))

            current_user.password = bcrypt.generate_password_hash(new_pass).decode('utf-8')
            db.session.commit()
            flash('password_updated', 'success')
            return redirect(url_for('settings'))

    return render_template('settings.html')

@app.route("/admin", methods=['GET', 'POST'])
@admin_required
def admin_panel():
    users = User.query.all()
    demo_recipe_exists = Recipe.query.filter_by(title='Chocolate Cake').first() is not None

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_demo_recipe':
            if create_demo_recipe():
                flash('Demo recipe created successfully!', 'success')
            else:
                flash('Demo recipe could not be created. Ensure demo user and ingredients exist.', 'danger')
            return redirect(url_for('admin_panel'))

        user_id = request.form.get('user_id')
        target_user = User.query.get(user_id)
        if not target_user or target_user.id == current_user.id:
            flash('Cannot modify this user.', 'danger')
            return redirect(url_for('admin_panel'))

        if action == 'toggle_admin':
            target_user.is_admin = not target_user.is_admin
            db.session.commit()
            flash('User updated.', 'success')
        elif action == 'toggle_pause':
            target_user.is_paused = not target_user.is_paused
            db.session.commit()
            flash('User updated.', 'success')
        elif action == 'delete_user':
            for recipe in Recipe.query.filter_by(creator_id=target_user.id).all():
                RecipeIngredient.query.filter_by(recipe_id=recipe.id).delete()
                RecipeStep.query.filter_by(recipe_id=recipe.id).delete()
                db.session.delete(recipe)
            for ingredient in Ingredient.query.filter_by(creator_id=target_user.id).all():
                UnitIngredient.query.filter_by(ingredient_id=ingredient.id).delete()
                IngredientTranslation.query.filter_by(ingredient_id=ingredient.id).delete()
                db.session.delete(ingredient)
            for unit in Unit.query.filter_by(creator_id=target_user.id).all():
                UnitIngredient.query.filter_by(unit_id=unit.id).delete()
                db.session.delete(unit)
            db.session.delete(target_user)
            db.session.commit()
            flash('User deleted.', 'success')

        return redirect(url_for('admin_panel'))

    return render_template('admin.html', users=users, demo_recipe_exists=demo_recipe_exists)

@app.route("/ingredient/new", methods=['GET', 'POST'])
@login_required
def add_ingredient():
    if request.method == 'POST':
        name_en = request.form.get('name_en', '').strip()
        name_de = request.form.get('name_de', '').strip()
        name_ru = request.form.get('name_ru', '').strip()

        if not name_en and not name_de and not name_ru:
            flash('Name is required', 'danger')
            return render_template('add_ingredient.html')

        primary_lang = request.form.get('language', session.get('lang', 'en'))
        name = name_en or name_de or name_ru

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
            unit_name = request.form.get('unit_name', '').strip()
            if unit_name:
                if not grams_val:
                    flash('Grams per unit is required when a unit name is provided', 'danger')
                    return render_template('add_ingredient.html')
                try:
                    grams_per_unit = float(grams_val)
                    if grams_per_unit <= 0:
                        raise ValueError()
                except ValueError:
                    flash('Grams per unit must be a positive number', 'danger')
                    return render_template('add_ingredient.html')

        comment = request.form.get('comment') or None
        language = request.form.get('language', session.get('lang', 'en'))

        try:
            new_ingredient = Ingredient(
                name=name,
                language=primary_lang,
                density=density,
                density_unit=density_type,
                grams_per_unit=grams_per_unit,
                unit_name=unit_name,
                comment=comment,
                creator_id=current_user.id
            )
            db.session.add(new_ingredient)
            db.session.flush()

            for lang in ['en', 'de', 'ru']:
                trans_name = request.form.get(f'name_{lang}', '').strip()
                if trans_name and lang != primary_lang:
                    existing = IngredientTranslation.query.filter_by(ingredient_id=new_ingredient.id, language=lang).first()
                    if existing:
                        existing.name = trans_name
                    else:
                        new_trans = IngredientTranslation(
                            ingredient_id=new_ingredient.id,
                            language=lang,
                            name=trans_name
                        )
                        db.session.add(new_trans)

            if density_type == 'g/unit' and unit_name and grams_per_unit:
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
    all_ingredients = get_all_ingredients()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        name_ru = request.form.get('name_ru', '').strip()
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
            name_ru=name_ru or None,
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
            if ingredient_ids[i]:
                override = grams_overrides[i] if i < len(grams_overrides) and grams_overrides[i] else grams_conversion
                ui = UnitIngredient(
                    unit_id=new_unit.id,
                    ingredient_id=int(ingredient_ids[i]),
                    grams_override=float(override)
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

@app.route("/manage", methods=['GET', 'POST'])
@login_required
def manage_data():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'edit_ingredient':
            ing_id = request.form.get('ingredient_id')
            ingredient = Ingredient.query.get(ing_id)
            if ingredient and (ingredient.creator_id == current_user.id or current_user.is_admin):
                ingredient.name = request.form.get('name', '').strip()
                density_val = request.form.get('density')
                if density_val:
                    try:
                        ingredient.density = float(density_val)
                    except ValueError:
                        pass
                ingredient.density_unit = request.form.get('density_unit', 'g/ml')
                grams_val = request.form.get('grams_per_unit')
                if grams_val:
                    try:
                        ingredient.grams_per_unit = float(grams_val)
                    except ValueError:
                        pass
                ingredient.unit_name = request.form.get('unit_name') or None
                ingredient.comment = request.form.get('comment') or None
                db.session.commit()
                flash('Ingredient updated!', 'success')

        elif action == 'edit_unit':
            unit_id = request.form.get('unit_id')
            unit = Unit.query.get(unit_id)
            if unit and (unit.creator_id == current_user.id or current_user.is_admin):
                unit.name = request.form.get('name', '').strip()
                unit.name_ru = request.form.get('name_ru', '').strip() or None
                unit.unit_type = request.form.get('unit_type')
                grams_val = request.form.get('grams_conversion')
                if grams_val:
                    try:
                        unit.grams_conversion = float(grams_val)
                    except ValueError:
                        pass
                unit.is_bound = True if request.form.get('is_bound') else False

                UnitIngredient.query.filter_by(unit_id=unit.id).delete()

                ingredient_ids = request.form.getlist('ingredient_id[]')
                grams_overrides = request.form.getlist('grams_override[]')
                for i in range(len(ingredient_ids)):
                    if ingredient_ids[i]:
                        override = grams_overrides[i] if i < len(grams_overrides) and grams_overrides[i] else unit.grams_conversion
                        ui = UnitIngredient(
                            unit_id=unit.id,
                            ingredient_id=int(ingredient_ids[i]),
                            grams_override=float(override)
                        )
                        db.session.add(ui)

                db.session.commit()
                flash('Unit updated!', 'success')

        elif action == 'add_translation':
            ing_id = request.form.get('ingredient_id')
            lang = request.form.get('language')
            trans_name = request.form.get('translation', '').strip()
            if ing_id and lang and trans_name:
                existing = IngredientTranslation.query.filter_by(ingredient_id=int(ing_id), language=lang).first()
                if existing:
                    existing.name = trans_name
                else:
                    new_trans = IngredientTranslation(
                        ingredient_id=int(ing_id),
                        language=lang,
                        name=trans_name
                    )
                    db.session.add(new_trans)
                db.session.commit()
                flash('Translation saved!', 'success')

        elif action == 'delete_ingredient':
            ing_id = request.form.get('ingredient_id')
            ingredient = Ingredient.query.get(ing_id)
            if ingredient and (ingredient.creator_id == current_user.id or current_user.is_admin):
                in_use_count = RecipeIngredient.query.filter_by(ingredient_id=ingredient.id).count()
                if in_use_count > 0:
                    flash(f'Cannot delete ingredient "{ingredient.get_name("en")}" — it is used in {in_use_count} recipe(s).', 'danger')
                else:
                    UnitIngredient.query.filter_by(ingredient_id=ingredient.id).delete()
                    IngredientTranslation.query.filter_by(ingredient_id=ingredient.id).delete()
                    db.session.delete(ingredient)
                    db.session.commit()
                    flash('Ingredient deleted.', 'success')

        elif action == 'delete_unit':
            unit_id = request.form.get('unit_id')
            unit = Unit.query.get(unit_id)
            if unit and (unit.creator_id == current_user.id or current_user.is_admin):
                in_use_count = RecipeIngredient.query.filter_by(unit_id=unit.id).count()
                if in_use_count > 0:
                    flash(f'Cannot delete unit "{unit.get_name("en")}" — it is used in {in_use_count} recipe(s).', 'danger')
                else:
                    UnitIngredient.query.filter_by(unit_id=unit.id).delete()
                    db.session.delete(unit)
                    db.session.commit()
                    flash('Unit deleted.', 'success')

        return redirect(url_for('manage_data'))

    if current_user.is_admin:
        ingredients = Ingredient.query.all()
        units = Unit.query.all()
    else:
        ingredients = Ingredient.query.filter(
            (Ingredient.creator_id == current_user.id) | (Ingredient.creator_id == None)
        ).all()
        units = Unit.query.filter(
            (Unit.creator_id == current_user.id) | (Unit.creator_id == None)
        ).all()

    return render_template('manage_data.html', ingredients=ingredients, units=units)

@app.route("/recipe/<int:recipe_id>/print")
def print_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    # Allow download if not draft, or if user is owner of the recipe
    if recipe.is_draft and (not current_user.is_authenticated or recipe.creator_id != current_user.id):
        abort(404)

    pdf_content = f"""# {recipe.title}

{recipe.description or ''}

**Portions:** {recipe.portions}

---

## Ingredients

"""
    for ri in recipe.recipe_ingredients:
        ing_name = ri.ingredient.get_name(session.get('lang', 'en'))
        unit_name = ri.unit.get_name(session.get('lang', 'en')) if ri.unit else 'g'
        amount_str = f"{ri.amount} {unit_name}"
        pdf_content += f"- {ing_name}: {amount_str}\n"

    if recipe.steps:
        pdf_content += "\n## Instructions\n\n"
        for step in recipe.steps:
            pdf_content += f"{step.step_number}. {step.instruction}\n\n"

    if recipe.instructions:
        pdf_content += f"\n{recipe.instructions}\n"

    filename = f"{recipe.title.replace(' ', '_')}.md"
    return pdf_content, 200, {
        'Content-Type': 'text/markdown; charset=utf-8',
        'Content-Disposition': f'attachment; filename="{filename}"'
    }

def create_demo_recipe():
    user = User.query.filter_by(username='demo').first()
    if not user:
        return False

    if Recipe.query.filter_by(title='Chocolate Cake').first():
        return False

    flour = Ingredient.query.filter_by(name='Flour (Type 405)').first()
    cocoa = Ingredient.query.filter_by(name='Cocoa Powder').first()
    sugar = Ingredient.query.filter_by(name='Sugar').first()
    oil = Ingredient.query.filter_by(name='Vegetable Oil').first()
    baking_powder = Ingredient.query.filter_by(name='Baking Powder').first()
    water = Ingredient.query.filter_by(name='Water').first()
    vanilla = Ingredient.query.filter_by(name='Vanilla Extract').first()
    g_unit = Unit.query.filter_by(name='g').first()
    ml_unit = Unit.query.filter_by(name='mL').first()
    tbsp_unit = Unit.query.filter_by(name='EL').first()
    tsp_unit = Unit.query.filter_by(name='TL').first()

    if not all([flour, cocoa, sugar, oil, baking_powder, water, vanilla, g_unit, ml_unit, tbsp_unit, tsp_unit]):
        return False

    cake_group_id = str(uuid.uuid4())

    recipe_en = Recipe(
        title='Chocolate Cake',
        description='A moist chocolate cake with cocoa powder, baked in a springform pan (Ø 20 cm).',
        instructions='Dust the cake with powdered sugar after it has cooled completely.',
        is_draft=False,
        portions=4,
        language='en',
        creator_id=user.id,
        group_id=cake_group_id
    )
    db.session.add(recipe_en)
    db.session.flush()

    db.session.add(RecipeIngredient(recipe_id=recipe_en.id, ingredient_id=flour.id, amount=250, unit_id=g_unit.id, step_number=1))
    db.session.add(RecipeIngredient(recipe_id=recipe_en.id, ingredient_id=cocoa.id, amount=3, unit_id=tbsp_unit.id, step_number=1))
    db.session.add(RecipeIngredient(recipe_id=recipe_en.id, ingredient_id=baking_powder.id, amount=2.5, unit_id=tsp_unit.id, step_number=1))
    db.session.add(RecipeIngredient(recipe_id=recipe_en.id, ingredient_id=sugar.id, amount=180, unit_id=g_unit.id, step_number=2))
    db.session.add(RecipeIngredient(recipe_id=recipe_en.id, ingredient_id=oil.id, amount=100, unit_id=ml_unit.id, step_number=2))
    db.session.add(RecipeIngredient(recipe_id=recipe_en.id, ingredient_id=water.id, amount=250, unit_id=ml_unit.id, step_number=2))
    db.session.add(RecipeIngredient(recipe_id=recipe_en.id, ingredient_id=vanilla.id, amount=1, unit_id=tsp_unit.id, step_number=2))

    db.session.add(RecipeStep(recipe_id=recipe_en.id, step_number=1, instruction='Preheat oven to 180°C (350°F) top/bottom heat (convection: 160°C/320°F). Grease a springform pan (Ø 20 cm) with a little oil. Mix flour with baking powder and cocoa powder.'))
    db.session.add(RecipeStep(recipe_id=recipe_en.id, step_number=2, instruction='Combine with the remaining ingredients and mix well. Pour batter into the springform pan. Bake in preheated oven for about 35 minutes. Test with a wooden skewer to see if done. Let cool completely.'))

    recipe_de = Recipe(
        title='Schokoladenkuchen',
        description='Ein saftiger Schokoladenkuchen mit Kakaopulver, gebacken in einer Springform (Ø 20 cm).',
        instructions='Den Kuchen nach dem vollständigen Abkühlen mit Puderzucker bestreuen.',
        is_draft=False,
        portions=4,
        language='de',
        creator_id=user.id,
        group_id=cake_group_id
    )
    db.session.add(recipe_de)
    db.session.flush()

    db.session.add(RecipeIngredient(recipe_id=recipe_de.id, ingredient_id=flour.id, amount=250, unit_id=g_unit.id, step_number=1))
    db.session.add(RecipeIngredient(recipe_id=recipe_de.id, ingredient_id=cocoa.id, amount=3, unit_id=tbsp_unit.id, step_number=1))
    db.session.add(RecipeIngredient(recipe_id=recipe_de.id, ingredient_id=baking_powder.id, amount=2.5, unit_id=tsp_unit.id, step_number=1))
    db.session.add(RecipeIngredient(recipe_id=recipe_de.id, ingredient_id=sugar.id, amount=180, unit_id=g_unit.id, step_number=2))
    db.session.add(RecipeIngredient(recipe_id=recipe_de.id, ingredient_id=oil.id, amount=100, unit_id=ml_unit.id, step_number=2))
    db.session.add(RecipeIngredient(recipe_id=recipe_de.id, ingredient_id=water.id, amount=250, unit_id=ml_unit.id, step_number=2))
    db.session.add(RecipeIngredient(recipe_id=recipe_de.id, ingredient_id=vanilla.id, amount=1, unit_id=tsp_unit.id, step_number=2))

    db.session.add(RecipeStep(recipe_id=recipe_de.id, step_number=1, instruction='Ofen auf 180°C (Ober-/Unterhitze, Umluft: 160°C) vorheizen. Eine Springform (Ø 20 cm) mit etwas Öl einfetten. Mehl mit Backpulver und Kakaopulver mischen.'))
    db.session.add(RecipeStep(recipe_id=recipe_de.id, step_number=2, instruction='Mit den restlichen Zutaten gut vermischen. Teig in die Springform füllen. Im vorgeheizten Ofen ca. 35 Minuten backen. Mit einem Holzstäbchen testen. Vollständig abkühlen lassen.'))

    recipe_ru = Recipe(
        title='Шоколадный торт',
        description='Влажный шоколадный торт с какао-порошком, выпеченный в разъёмной форме (Ø 20 см).',
        instructions='Посыпьте торт сахарной пудрой после полного остывания.',
        is_draft=False,
        portions=4,
        language='ru',
        creator_id=user.id,
        group_id=cake_group_id
    )
    db.session.add(recipe_ru)
    db.session.flush()

    db.session.add(RecipeIngredient(recipe_id=recipe_ru.id, ingredient_id=flour.id, amount=250, unit_id=g_unit.id, step_number=1))
    db.session.add(RecipeIngredient(recipe_id=recipe_ru.id, ingredient_id=cocoa.id, amount=3, unit_id=tbsp_unit.id, step_number=1))
    db.session.add(RecipeIngredient(recipe_id=recipe_ru.id, ingredient_id=baking_powder.id, amount=2.5, unit_id=tsp_unit.id, step_number=1))
    db.session.add(RecipeIngredient(recipe_id=recipe_ru.id, ingredient_id=sugar.id, amount=180, unit_id=g_unit.id, step_number=2))
    db.session.add(RecipeIngredient(recipe_id=recipe_ru.id, ingredient_id=oil.id, amount=100, unit_id=ml_unit.id, step_number=2))
    db.session.add(RecipeIngredient(recipe_id=recipe_ru.id, ingredient_id=water.id, amount=250, unit_id=ml_unit.id, step_number=2))
    db.session.add(RecipeIngredient(recipe_id=recipe_ru.id, ingredient_id=vanilla.id, amount=1, unit_id=tsp_unit.id, step_number=2))

    db.session.add(RecipeStep(recipe_id=recipe_ru.id, step_number=1, instruction='Разогрейте духовку до 180°C (верхний/нижний жар, конвекция: 160°C). Смажьте разъёмную форму (Ø 20 см) небольшим количеством масла. Смешайте муку с разрыхлителем и какао-порошком.'))
    db.session.add(RecipeStep(recipe_id=recipe_ru.id, step_number=2, instruction='Смешайте с оставшимися ингредиентами до однородности. Вылейте тесто в форму. Выпекайте в духовке около 35 минут. Проверьте деревянной шпажкой. Полностью остудите.'))

    db.session.commit()
    return True

def init_db():
    with app.app_context():
        db.create_all()

        user = User.query.first()
        if not user:
            user = User(username='demo', password=bcrypt.generate_password_hash('demo').decode('utf-8'), is_admin=True)
            db.session.add(user)
            db.session.commit()

        g_unit = Unit.query.filter_by(name='g').first()
        if not g_unit:
            g_unit = Unit(name='g', name_ru='г', unit_type='mass', grams_conversion=1.0, creator_id=user.id)
            db.session.add(g_unit)

        ml_unit = Unit.query.filter_by(name='mL').first()
        if not ml_unit:
            ml_unit = Unit(name='mL', name_ru='мл', unit_type='volume', grams_conversion=1.0, creator_id=user.id)
            db.session.add(ml_unit)

        tbsp_unit = Unit.query.filter_by(name='EL').first()
        if not tbsp_unit:
            tbsp_unit = Unit(name='EL', name_ru='ст.л.', unit_type='volume', grams_conversion=15.0, creator_id=user.id)
            db.session.add(tbsp_unit)

        tsp_unit = Unit.query.filter_by(name='TL').first()
        if not tsp_unit:
            tsp_unit = Unit(name='TL', name_ru='ч.л.', unit_type='volume', grams_conversion=5.0, creator_id=user.id)
            db.session.add(tsp_unit)

        db.session.commit()

        if not Ingredient.query.filter_by(name='Flour (Type 405)').first():
            flour = Ingredient(name='Flour (Type 405)', language='en', density=0.55, density_unit='g/ml', creator_id=user.id)
            db.session.add(flour)
            db.session.flush()
            db.session.add(IngredientTranslation(ingredient_id=flour.id, language='de', name='Mehl (Type 405)'))
            db.session.add(IngredientTranslation(ingredient_id=flour.id, language='ru', name='Мука (тип 405)'))

            cocoa = Ingredient(name='Cocoa Powder', language='en', density=0.4, density_unit='g/ml', creator_id=user.id)
            db.session.add(cocoa)
            db.session.flush()
            db.session.add(IngredientTranslation(ingredient_id=cocoa.id, language='de', name='Kakaopulver'))
            db.session.add(IngredientTranslation(ingredient_id=cocoa.id, language='ru', name='Какао-порошок'))

            sugar = Ingredient(name='Sugar', language='en', density=0.85, density_unit='g/ml', creator_id=user.id)
            db.session.add(sugar)
            db.session.flush()
            db.session.add(IngredientTranslation(ingredient_id=sugar.id, language='de', name='Zucker'))
            db.session.add(IngredientTranslation(ingredient_id=sugar.id, language='ru', name='Сахар'))

            oil = Ingredient(name='Vegetable Oil', language='en', density=0.92, density_unit='g/ml', creator_id=user.id)
            db.session.add(oil)
            db.session.flush()
            db.session.add(IngredientTranslation(ingredient_id=oil.id, language='de', name='Pflanzenöl'))
            db.session.add(IngredientTranslation(ingredient_id=oil.id, language='ru', name='Растительное масло'))

            baking_powder = Ingredient(name='Baking Powder', language='en', density=0.9, density_unit='g/ml', creator_id=user.id)
            db.session.add(baking_powder)
            db.session.flush()
            db.session.add(IngredientTranslation(ingredient_id=baking_powder.id, language='de', name='Backpulver'))
            db.session.add(IngredientTranslation(ingredient_id=baking_powder.id, language='ru', name='Разрыхлитель'))

            water = Ingredient(name='Water', language='en', density=1.0, density_unit='g/ml', creator_id=user.id)
            db.session.add(water)
            db.session.flush()
            db.session.add(IngredientTranslation(ingredient_id=water.id, language='de', name='Wasser'))
            db.session.add(IngredientTranslation(ingredient_id=water.id, language='ru', name='Вода'))

            vanilla = Ingredient(name='Vanilla Extract', language='en', density=1.06, density_unit='g/ml', creator_id=user.id)
            db.session.add(vanilla)
            db.session.flush()
            db.session.add(IngredientTranslation(ingredient_id=vanilla.id, language='de', name='Vanilleextrakt'))
            db.session.add(IngredientTranslation(ingredient_id=vanilla.id, language='ru', name='Ванильный экстракт'))

            db.session.commit()

        if not Recipe.query.first():
            create_demo_recipe()

init_db()

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_ENV') != 'production', port=5000)
