from flask import Flask, render_template, redirect, url_for, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
bcrypt = Bcrypt(app)

# --- Models ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    # Track things created by the user
    recipes = db.relationship('Recipe', backref='author', lazy=True)
    ingredients = db.relationship('Ingredient', backref='author', lazy=True)

class Ingredient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    density = db.Column(db.Float, nullable=True) # grams per ml/unit
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Recipe(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    instructions = db.Column(db.Text, nullable=False)
    is_draft = db.Column(db.Boolean, default=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # Relationship to the association model
    recipe_ingredients = db.relationship('RecipeIngredient', backref='recipe', cascade="all, delete-orphan", lazy=True)

class RecipeIngredient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey('recipe.id'), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('ingredient.id'), nullable=False)
    amount_grams = db.Column(db.Float, nullable=False)
    display_unit = db.Column(db.String(20), nullable=False) # e.g., 'g', 'ml', 'pcs'
    
    # Link to the actual ingredient for easy access
    ingredient = db.relationship('Ingredient')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Routes ---

@app.route("/")
def home():
    # Show all non-draft recipes on home page
    recipes = Recipe.query.filter_by(is_draft=False).order_by(Recipe.id.desc()).all()
    return render_template('home.html', recipes=recipes)

@app.route("/recipe/<int:recipe_id>")
@login_required
def recipe_detail(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    all_ingredients = Ingredient.query.all() # Added to provide list for the dropdown
    return render_template('recipe_detail.html', recipe=recipe, all_ingredients=all_ingredients)

@app.route("/recipe/new", methods=['GET', 'POST'])
@login_required
def add_recipe():
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        instructions = request.form.get('instructions')
        is_draft = True if request.form.get('is_draft') else False
        
        new_recipe = Recipe(
            title=title, 
            description=description, 
            instructions=instructions, 
            is_draft=is_draft, 
            author=current_user
        )
        db.session.add(new_recipe)
        db.session.commit()
        flash('Recipe created!', 'success')
        return redirect(url_for('home'))
    return render_template('add_recipe.html')

@app.route("/recipe/<int:recipe_id>/add_ingredient", methods=['POST'])
@login_required
def add_ingredient_to_recipe(recipe_id):
    ingredient_id = request.form.get('ingredient_id')
    amount = request.form.get('amount')
    unit = request.form.get('unit')
    
    if ingredient_id and amount and unit:
        ri = RecipeIngredient(
            recipe_id=recipe_id,
            ingredient_id=ingredient_id,
            amount_grams=float(amount),
            display_unit=unit
        )
        db.session.add(ri)
        db.session.commit()
        flash('Ingredient added to recipe!', 'success')
    
    return redirect(url_for('recipe_detail', recipe_id=recipe_id))

@app.route("/ingredient/new", methods=['GET', 'POST'])
@login_required
def add_ingredient():
    if request.method == 'POST':
        name = request.form.get('name')
        density = request.form.get('density') # Fixed: changed request.params to request.form
        
        new_ing = Ingredient(name=name, density=float(density) if density else None, author=current_user)
        db.session.add(new_ing)
        db.session.commit()
        flash('Ingredient added!', 'success')
        return redirect(url_for('home'))
    return render_template('add_ingredient.html')

@app.route("/login", methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(for_url('home'))
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
        user = User.query.filter_by(username=username).first() # Fixed: changed template to username
        if user:
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=8001)
