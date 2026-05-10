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
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    unit_associations = db.relationship('UnitIngredient', backref='ingredient', cascade="all, delete-orphan", lazy=True)

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
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipe_ingredients = db.relationship('RecipeIngredient', backref='recipe', cascade="all, delete-orphan", lazy=True)
    steps = db.relationship('RecipeStep', backref='recipe', cascade="all, delete-orphan", lazy=True, order_by='RecipeStep.step_number')

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
            if ing_id not in aggregated:
                aggregated[ing_id] = {
                    'ingredient': ri.ingredient,
                    'total_amount': 0,
                    'total_grams': 0,
                    'display_unit': ri.display_unit,
                    'steps': []
                }
            aggregated[ing_id]['total_amount'] += ri.amount
            aggregated[ing_id]['total_grams'] += ri.amount_grams
            if ri.step_number is not None:
                aggregated[ing_id]['steps'].append({
                    'step_number': ri.step_number,
                    'amount': ri.amount,
                    'display_unit': ri.display_unit
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

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Routes ---

@app.route("/")
def home():
    if current_user.is_authenticated:
        # Show non-drafts OR drafts owned by the user
        recipes = Recipe.query.filter(
            (Recipe.is_draft == False) | (Recipe.creator_id == current_user.id)
        ).order_by(Recipe.id.desc()).all()
    else:
        recipes = Recipe.query.filter_by(is_draft=False).order_by(Recipe.id.desc()).all()
    return render_template('home.html', recipes=recipes)

@app.route("/recipe/<int:recipe_id>")
@login_required
def recipe_detail(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    return render_template('recipe_detail.html', recipe=recipe)

@app.route("/recipe/new", methods=['GET', 'POST'])
@login_required
def add_recipe():
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        instructions = request.form.get('instructions') or ''
        is_draft = True if request.form.get('is_draft') else False

        new_recipe = Recipe(
            title=title,
            description=description,
            instructions=instructions,
            is_draft=is_draft,
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
            all_ingredients = [{'id': i.id, 'name': i.name} for i in Ingredient.query.all()]
            all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
            return render_template('add_recipe.html', all_ingredients=all_ingredients, all_units=all_units)

    all_ingredients = [{'id': i.id, 'name': i.name} for i in Ingredient.query.all()]
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
        recipe.title = request.form.get('title')
        recipe.description = request.form.get('description')
        recipe.instructions = request.form.get('instructions') or ''
        recipe.is_draft = True if request.form.get('is_draft') else False

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
            all_ingredients = [{'id': i.id, 'name': i.name} for i in Ingredient.query.all()]
            all_units = [{'id': u.id, 'name': u.name, 'unit_type': u.unit_type, 'is_bound': u.is_bound, 'ingredients': [ui.ingredient_id for ui in u.ingredient_units]} for u in Unit.query.all()]
            return render_template('edit_recipe.html', recipe=recipe, all_ingredients=all_ingredients, all_units=all_units)

    all_ingredients = [{'id': i.id, 'name': i.name} for i in Ingredient.query.all()]
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
        if User.query.filter_by(username=username).first():
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
        name = request.form.get('name')
        density_type = request.form.get('density_type')
        density = None
        grams_per_unit = None
        unit_name = None
        
        if density_type == 'g/ml':
            density = float(request.form.get('density')) if request.form.get('density') else None
        elif density_type == 'g/unit':
            grams_per_unit = float(request.form.get('grams_per_unit')) if request.form.get('grams_per_unit') else None
            unit_name = request.form.get('unit_name') or None
        
        new_ingredient = Ingredient(
            name=name,
            density=density,
            density_unit=density_type,
            grams_per_unit=grams_per_unit,
            unit_name=unit_name,
            creator_id=current_user.id
        )
        db.session.add(new_ingredient)
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
    all_ingredients = [{'id': i.id, 'name': i.name} for i in Ingredient.query.all()]
    if request.method == 'POST':
        name = request.form.get('name')
        unit_type = request.form.get('unit_type')
        grams_conversion = float(request.form.get('grams_conversion'))
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not Unit.query.filter_by(name='g').first():
            db.session.add(Unit(name='g', unit_type='mass', grams_conversion=1.0, creator_id=1))
        if not Unit.query.filter_by(name='mL').first():
            db.session.add(Unit(name='mL', unit_type='volume', grams_conversion=1.0, creator_id=1))
        db.session.commit()
    app.run(debug=True, port=8001)
