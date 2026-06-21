import json
import os
import uuid
import datetime
from flask import Flask, render_template, request, redirect, url_for, Response, flash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'trip-expenses-secret')

# DATA_DIR: use /data (Railway/Render persistent volume) if it exists, else local data/
_data_dir = '/data' if os.path.isdir('/data') else os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(_data_dir, exist_ok=True)
DATA_FILE = os.path.join(_data_dir, 'trips.json')


def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, 'r') as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def calculate_settlement(trip):
    participants = trip['participants']
    expenses = trip['expenses']

    paid = {p: 0.0 for p in participants}
    share = {p: 0.0 for p in participants}

    for exp in expenses:
        payer = exp['paid_by']
        amount = float(exp['amount'])
        splitters = exp.get('split_among', participants)
        if not splitters:
            splitters = participants
        per_head = amount / len(splitters)
        paid[payer] = paid.get(payer, 0) + amount
        for p in splitters:
            share[p] = share.get(p, 0) + per_head

    total_spent = sum(paid.values())

    settlement = []
    for p in participants:
        dues = paid.get(p, 0) - share.get(p, 0)
        settlement.append({
            'name': p,
            'paid': round(paid.get(p, 0), 2),
            'share': round(share.get(p, 0), 2),
            'dues': round(dues, 2),
        })

    transfers = simplify_debts(settlement)

    return {
        'total_spent': round(total_spent, 2),
        'per_head_equal': round(total_spent / len(participants), 2) if participants else 0,
        'settlement': settlement,
        'transfers': transfers,
    }


def simplify_debts(settlement):
    creditors = []
    debtors = []
    for s in settlement:
        if s['dues'] > 0.01:
            creditors.append({'name': s['name'], 'amount': s['dues']})
        elif s['dues'] < -0.01:
            debtors.append({'name': s['name'], 'amount': -s['dues']})

    transfers = []
    i, j = 0, 0
    creditors = sorted(creditors, key=lambda x: -x['amount'])
    debtors = sorted(debtors, key=lambda x: -x['amount'])

    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i]['amount'], creditors[j]['amount'])
        transfers.append({
            'from': debtors[i]['name'],
            'to': creditors[j]['name'],
            'amount': round(amount, 2),
        })
        debtors[i]['amount'] -= amount
        creditors[j]['amount'] -= amount
        if debtors[i]['amount'] < 0.01:
            i += 1
        if creditors[j]['amount'] < 0.01:
            j += 1

    return transfers


@app.route('/')
def index():
    data = load_data()
    trips = [{'id': k, **v} for k, v in data.items()]
    trips.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return render_template('index.html', trips=trips)


@app.route('/trip/new', methods=['GET', 'POST'])
def new_trip():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        participants = [p.strip() for p in request.form.getlist('participants') if p.strip()]
        if not name or len(participants) < 2:
            return render_template('new_trip.html', error='Please enter a trip name and at least 2 participants.')
        data = load_data()
        trip_id = str(uuid.uuid4())[:8]
        data[trip_id] = {
            'name': name,
            'participants': participants,
            'expenses': [],
            'created_at': datetime.datetime.now().isoformat(),
        }
        save_data(data)
        return redirect(url_for('trip_detail', trip_id=trip_id))
    return render_template('new_trip.html')


@app.route('/trip/<trip_id>')
def trip_detail(trip_id):
    data = load_data()
    trip = data.get(trip_id)
    if not trip:
        return redirect(url_for('index'))
    summary = calculate_settlement(trip)
    return render_template('trip.html', trip=trip, trip_id=trip_id, summary=summary)


@app.route('/trip/<trip_id>/add_expense', methods=['POST'])
def add_expense(trip_id):
    data = load_data()
    trip = data.get(trip_id)
    if not trip:
        return redirect(url_for('index'))

    description = request.form.get('description', '').strip()
    paid_by = request.form.get('paid_by', '').strip()
    amount = request.form.get('amount', '0').strip()
    split_among = request.form.getlist('split_among')

    try:
        amount = float(amount)
    except ValueError:
        return redirect(url_for('trip_detail', trip_id=trip_id))

    if not split_among:
        split_among = trip['participants']

    expense = {
        'id': str(uuid.uuid4())[:8],
        'description': description,
        'paid_by': paid_by,
        'amount': amount,
        'split_among': split_among,
    }
    trip['expenses'].append(expense)
    save_data(data)
    return redirect(url_for('trip_detail', trip_id=trip_id))


@app.route('/trip/<trip_id>/delete_expense/<expense_id>', methods=['POST'])
def delete_expense(trip_id, expense_id):
    data = load_data()
    trip = data.get(trip_id)
    if trip:
        trip['expenses'] = [e for e in trip['expenses'] if e['id'] != expense_id]
        save_data(data)
    return redirect(url_for('trip_detail', trip_id=trip_id))


@app.route('/trip/<trip_id>/delete', methods=['POST'])
def delete_trip(trip_id):
    data = load_data()
    data.pop(trip_id, None)
    save_data(data)
    return redirect(url_for('index'))


@app.route('/trip/<trip_id>/export')
def export_trip(trip_id):
    data = load_data()
    trip = data.get(trip_id)
    if not trip:
        return redirect(url_for('index'))

    export_data = {
        'trip_id': trip_id,
        'exported_at': datetime.datetime.now().isoformat(),
        **trip,
    }
    filename = trip['name'].replace(' ', '_').lower() + '_export.json'
    return Response(
        json.dumps(export_data, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@app.route('/import', methods=['GET', 'POST'])
def import_trip():
    if request.method == 'POST':
        file = request.files.get('trip_file')
        if not file or file.filename == '':
            flash('Please select a file to import.', 'error')
            return render_template('import_trip.html')

        try:
            content = file.read().decode('utf-8')
            import_data = json.loads(content)
        except Exception:
            flash('Invalid file. Please upload a valid trip export JSON file.', 'error')
            return render_template('import_trip.html')

        required = {'name', 'participants', 'expenses'}
        if not required.issubset(import_data.keys()):
            flash('File is missing required fields (name, participants, expenses).', 'error')
            return render_template('import_trip.html')

        data = load_data()
        # Reuse original trip_id if not already taken, otherwise generate new one
        trip_id = import_data.get('trip_id', str(uuid.uuid4())[:8])
        if trip_id in data:
            trip_id = str(uuid.uuid4())[:8]

        data[trip_id] = {
            'name': import_data['name'],
            'participants': import_data['participants'],
            'expenses': import_data['expenses'],
            'created_at': import_data.get('created_at', datetime.datetime.now().isoformat()),
            'imported_at': datetime.datetime.now().isoformat(),
        }
        save_data(data)
        flash(f'Trip "{import_data["name"]}" imported successfully!', 'success')
        return redirect(url_for('trip_detail', trip_id=trip_id))

    return render_template('import_trip.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)
