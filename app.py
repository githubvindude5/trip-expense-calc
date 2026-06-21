import json
import os
import uuid
import datetime
import io
from flask import Flask, render_template, request, redirect, url_for, Response, flash, session, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'trip-expenses-secret')

# DATA_DIR: use /data (Railway/Render persistent volume) if it exists, else local data/
_data_dir = '/data' if os.path.isdir('/data') else os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(_data_dir, exist_ok=True)
os.makedirs(os.path.join(_data_dir, 'photos'), exist_ok=True)
DATA_FILE = os.path.join(_data_dir, 'trips.json')

ALLOWED_PHOTO_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}


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
        creator_password = request.form.get('creator_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()

        if not name or len(participants) < 2:
            return render_template('new_trip.html', error='Please enter a trip name and at least 2 participants.')

        if creator_password and creator_password != confirm_password:
            return render_template('new_trip.html', error='Passwords do not match.')

        data = load_data()
        trip_id = str(uuid.uuid4())[:8]
        trip = {
            'name': name,
            'participants': participants,
            'expenses': [],
            'created_at': datetime.datetime.now().isoformat(),
        }
        if creator_password:
            trip['creator_password'] = generate_password_hash(creator_password)

        data[trip_id] = trip
        save_data(data)

        if creator_password:
            session[f'creator_{trip_id}'] = True

        return redirect(url_for('trip_detail', trip_id=trip_id))
    return render_template('new_trip.html')


@app.route('/trip/<trip_id>')
def trip_detail(trip_id):
    data = load_data()
    trip = data.get(trip_id)
    if not trip:
        return redirect(url_for('index'))
    summary = calculate_settlement(trip)
    is_creator = session.get(f'creator_{trip_id}', False)
    has_password = bool(trip.get('creator_password'))
    return render_template('trip.html', trip=trip, trip_id=trip_id, summary=summary,
                           is_creator=is_creator, has_password=has_password)


@app.route('/trip/<trip_id>/creator_login', methods=['POST'])
def creator_login(trip_id):
    data = load_data()
    trip = data.get(trip_id)
    if not trip:
        return redirect(url_for('index'))
    password = request.form.get('password', '')
    if trip.get('creator_password') and check_password_hash(trip['creator_password'], password):
        session[f'creator_{trip_id}'] = True
    else:
        flash('Incorrect password.', 'error')
    return redirect(url_for('trip_detail', trip_id=trip_id))


@app.route('/trip/<trip_id>/creator_logout', methods=['POST'])
def creator_logout(trip_id):
    session.pop(f'creator_{trip_id}', None)
    return redirect(url_for('trip_detail', trip_id=trip_id))


@app.route('/trip/<trip_id>/add_participant', methods=['POST'])
def add_participant(trip_id):
    data = load_data()
    trip = data.get(trip_id)
    if not trip:
        return redirect(url_for('index'))
    person = request.form.get('person', '').strip()
    if person and person not in trip['participants'] and len(trip['participants']) < 10:
        trip['participants'].append(person)
        save_data(data)
    return redirect(url_for('trip_detail', trip_id=trip_id))


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
    expense_date = request.form.get('expense_date', '').strip()
    if not expense_date:
        expense_date = datetime.date.today().isoformat()

    try:
        amount = float(amount)
    except ValueError:
        return redirect(url_for('trip_detail', trip_id=trip_id))

    if not split_among:
        split_among = trip['participants']

    expense_id = str(uuid.uuid4())[:8]
    expense = {
        'id': expense_id,
        'description': description,
        'paid_by': paid_by,
        'amount': amount,
        'split_among': split_among,
        'date': expense_date,
    }

    # Handle photo upload
    photo = request.files.get('photo')
    if photo and photo.filename:
        ext = photo.filename.rsplit('.', 1)[-1].lower() if '.' in photo.filename else ''
        if ext in ALLOWED_PHOTO_EXTENSIONS:
            photo_path = os.path.join(_data_dir, 'photos', f'{expense_id}.{ext}')
            photo.save(photo_path)
            expense['photo_ext'] = ext

    trip['expenses'].append(expense)
    save_data(data)
    return redirect(url_for('trip_detail', trip_id=trip_id))


@app.route('/photo/<expense_id>')
def serve_photo(expense_id):
    # Find photo file for this expense_id
    photos_dir = os.path.join(_data_dir, 'photos')
    for ext in ALLOWED_PHOTO_EXTENSIONS:
        path = os.path.join(photos_dir, f'{expense_id}.{ext}')
        if os.path.exists(path):
            return send_file(path)
    return '', 404


@app.route('/trip/<trip_id>/delete_expense/<expense_id>', methods=['POST'])
def delete_expense(trip_id, expense_id):
    data = load_data()
    trip = data.get(trip_id)
    if trip:
        is_creator = session.get(f'creator_{trip_id}', False)
        has_password = bool(trip.get('creator_password'))
        if is_creator or not has_password:
            # Also delete photo if exists
            for ext in ALLOWED_PHOTO_EXTENSIONS:
                photo_path = os.path.join(_data_dir, 'photos', f'{expense_id}.{ext}')
                if os.path.exists(photo_path):
                    try:
                        os.remove(photo_path)
                    except Exception:
                        pass
            trip['expenses'] = [e for e in trip['expenses'] if e['id'] != expense_id]
            save_data(data)
    return redirect(url_for('trip_detail', trip_id=trip_id))


@app.route('/trip/<trip_id>/delete', methods=['POST'])
def delete_trip(trip_id):
    data = load_data()
    trip = data.get(trip_id)
    if trip:
        is_creator = session.get(f'creator_{trip_id}', False)
        has_password = bool(trip.get('creator_password'))
        if is_creator or not has_password:
            # Delete all photos for this trip
            for exp in trip.get('expenses', []):
                exp_id = exp.get('id', '')
                for ext in ALLOWED_PHOTO_EXTENSIONS:
                    photo_path = os.path.join(_data_dir, 'photos', f'{exp_id}.{ext}')
                    if os.path.exists(photo_path):
                        try:
                            os.remove(photo_path)
                        except Exception:
                            pass
            data.pop(trip_id, None)
            save_data(data)
            session.pop(f'creator_{trip_id}', None)
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


@app.route('/trip/<trip_id>/download_pdf')
def download_pdf(trip_id):
    data = load_data()
    trip = data.get(trip_id)
    if not trip:
        return redirect(url_for('index'))

    summary = calculate_settlement(trip)
    now = datetime.datetime.now()
    downloaded_at = now.strftime('%d %B %Y, %I:%M %p')

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )

    styles = getSampleStyleSheet()
    BLUE   = colors.HexColor('#1a73e8')
    DBLUE  = colors.HexColor('#174ea6')
    LGRAY  = colors.HexColor('#f1f3f4')
    MGRAY  = colors.HexColor('#dadce0')
    GREEN  = colors.HexColor('#188038')
    RED    = colors.HexColor('#d93025')
    WHITE  = colors.white
    BLACK  = colors.HexColor('#202124')

    title_style = ParagraphStyle('title', fontSize=22, textColor=WHITE,
                                  fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=4)
    sub_style   = ParagraphStyle('sub',   fontSize=10, textColor=colors.HexColor('#e8eaed'),
                                  fontName='Helvetica', alignment=TA_CENTER)
    h2_style    = ParagraphStyle('h2',    fontSize=13, textColor=DBLUE,
                                  fontName='Helvetica-Bold', spaceBefore=14, spaceAfter=6)
    normal      = ParagraphStyle('norm',  fontSize=9,  textColor=BLACK,
                                  fontName='Helvetica', leading=13)
    footer_style= ParagraphStyle('foot',  fontSize=8,  textColor=colors.HexColor('#80868b'),
                                  fontName='Helvetica', alignment=TA_CENTER)

    elements = []

    # Header banner
    header_data = [[
        Paragraph(f'  {trip["name"]}', title_style),
    ]]
    header_table = Table(header_data, colWidths=[17*cm])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), BLUE),
        ('ROUNDEDCORNERS', [8]),
        ('TOPPADDING',    (0,0), (-1,-1), 14),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING',   (0,0), (-1,-1), 12),
        ('RIGHTPADDING',  (0,0), (-1,-1), 12),
    ]))
    elements.append(header_table)

    sub_data = [[Paragraph(f'Downloaded on {downloaded_at}', sub_style)]]
    sub_table = Table(sub_data, colWidths=[17*cm])
    sub_table.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), DBLUE),
        ('TOPPADDING',    (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING',   (0,0), (-1,-1), 12),
        ('RIGHTPADDING',  (0,0), (-1,-1), 12),
    ]))
    elements.append(sub_table)
    elements.append(Spacer(1, 0.4*cm))

    # Summary cards row
    elements.append(Paragraph('Summary', h2_style))
    card_data = [[
        Paragraph(f'<b>Total Spent</b><br/>Rs. {summary["total_spent"]:,.2f}', normal),
        Paragraph(f'<b>Participants</b><br/>{len(trip["participants"])} people', normal),
        Paragraph(f'<b>Equal Split</b><br/>Rs. {summary["per_head_equal"]:,.2f} / person', normal),
        Paragraph(f'<b>Expenses</b><br/>{len(trip["expenses"])} entries', normal),
    ]]
    card_table = Table(card_data, colWidths=[4.25*cm]*4)
    card_table.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), LGRAY),
        ('BOX',           (0,0), (-1,-1), 0.5, MGRAY),
        ('INNERGRID',     (0,0), (-1,-1), 0.5, MGRAY),
        ('TOPPADDING',    (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
    ]))
    elements.append(card_table)
    elements.append(Spacer(1, 0.4*cm))

    # Expenses table
    elements.append(Paragraph('Expense Details', h2_style))
    exp_header = ['#', 'Description', 'Date', 'Paid By', 'Split Among', 'Amount (Rs.)']
    exp_rows = [exp_header]
    for i, exp in enumerate(trip['expenses'], 1):
        splitters = exp.get('split_among', trip['participants'])
        split_str = ', '.join(splitters) if splitters != trip['participants'] else 'All'
        exp_date = exp.get('date', '')

        # Build description cell - include thumbnail if photo exists
        desc_content = [Paragraph(exp['description'], normal)]
        photo_ext = exp.get('photo_ext', '')
        if photo_ext:
            photo_path = os.path.join(_data_dir, 'photos', f'{exp["id"]}.{photo_ext}')
            if os.path.exists(photo_path):
                try:
                    img = RLImage(photo_path)
                    img_w, img_h = img.imageWidth, img.imageHeight
                    max_w = 3 * cm
                    scale = min(max_w / img_w, max_w / img_h) if img_w and img_h else 1
                    img.drawWidth = img_w * scale
                    img.drawHeight = img_h * scale
                    desc_content.append(img)
                except Exception:
                    pass

        exp_rows.append([
            str(i),
            desc_content if len(desc_content) > 1 else Paragraph(exp['description'], normal),
            Paragraph(exp_date, normal),
            exp['paid_by'],
            Paragraph(split_str, normal),
            f'{float(exp["amount"]):,.2f}',
        ])

    exp_table = Table(exp_rows, colWidths=[0.7*cm, 4.5*cm, 2*cm, 2.5*cm, 4*cm, 2.5*cm])
    exp_style = TableStyle([
        ('BACKGROUND',    (0,0), (-1,0),  BLUE),
        ('TEXTCOLOR',     (0,0), (-1,0),  WHITE),
        ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,0),  9),
        ('ALIGN',         (0,0), (-1,0),  'CENTER'),
        ('ROWBACKGROUNDS',(0,1), (-1,-1), [WHITE, LGRAY]),
        ('FONTSIZE',      (0,1), (-1,-1), 8),
        ('ALIGN',         (5,1), (5,-1),  'RIGHT'),
        ('ALIGN',         (0,1), (0,-1),  'CENTER'),
        ('GRID',          (0,0), (-1,-1), 0.4, MGRAY),
        ('TOPPADDING',    (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING',   (0,0), (-1,-1), 6),
        ('RIGHTPADDING',  (0,0), (-1,-1), 6),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
    ])
    exp_table.setStyle(exp_style)
    elements.append(exp_table)
    elements.append(Spacer(1, 0.4*cm))

    # Balance table
    elements.append(Paragraph('Balance per Person', h2_style))
    bal_header = ['Name', 'Total Paid (Rs.)', 'Fair Share (Rs.)', 'Balance (Rs.)', 'Status']
    bal_rows = [bal_header]
    for s in summary['settlement']:
        balance = s['dues']
        status = 'Gets back' if balance > 0.01 else ('Owes' if balance < -0.01 else 'Settled')
        bal_rows.append([
            s['name'],
            f'{s["paid"]:,.2f}',
            f'{s["share"]:,.2f}',
            f'{abs(balance):,.2f}',
            status,
        ])

    bal_table = Table(bal_rows, colWidths=[3.5*cm, 3.5*cm, 3.5*cm, 3.5*cm, 3*cm])
    bal_style = TableStyle([
        ('BACKGROUND',    (0,0), (-1,0),  BLUE),
        ('TEXTCOLOR',     (0,0), (-1,0),  WHITE),
        ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,0),  9),
        ('ALIGN',         (0,0), (-1,0),  'CENTER'),
        ('ROWBACKGROUNDS',(0,1), (-1,-1), [WHITE, LGRAY]),
        ('FONTSIZE',      (0,1), (-1,-1), 8),
        ('ALIGN',         (1,1), (3,-1),  'RIGHT'),
        ('GRID',          (0,0), (-1,-1), 0.4, MGRAY),
        ('TOPPADDING',    (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING',   (0,0), (-1,-1), 6),
        ('RIGHTPADDING',  (0,0), (-1,-1), 6),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
    ])
    for row_i, s in enumerate(summary['settlement'], 1):
        if s['dues'] > 0.01:
            bal_style.add('TEXTCOLOR', (4, row_i), (4, row_i), GREEN)
            bal_style.add('FONTNAME',  (4, row_i), (4, row_i), 'Helvetica-Bold')
        elif s['dues'] < -0.01:
            bal_style.add('TEXTCOLOR', (4, row_i), (4, row_i), RED)
            bal_style.add('FONTNAME',  (4, row_i), (4, row_i), 'Helvetica-Bold')
    bal_table.setStyle(bal_style)
    elements.append(bal_table)
    elements.append(Spacer(1, 0.4*cm))

    # Settlement transfers
    if summary['transfers']:
        elements.append(Paragraph('Settlement Transfers', h2_style))
        txn_header = ['From', 'To', 'Amount (Rs.)']
        txn_rows = [txn_header]
        for t in summary['transfers']:
            txn_rows.append([t['from'], t['to'], f'{t["amount"]:,.2f}'])

        txn_table = Table(txn_rows, colWidths=[6*cm, 6*cm, 5*cm])
        txn_table.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,0),  colors.HexColor('#34a853')),
            ('TEXTCOLOR',     (0,0), (-1,0),  WHITE),
            ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0,0), (-1,0),  9),
            ('ALIGN',         (0,0), (-1,0),  'CENTER'),
            ('ROWBACKGROUNDS',(0,1), (-1,-1), [WHITE, colors.HexColor('#e6f4ea')]),
            ('FONTSIZE',      (0,1), (-1,-1), 9),
            ('ALIGN',         (2,1), (2,-1),  'RIGHT'),
            ('GRID',          (0,0), (-1,-1), 0.4, MGRAY),
            ('TOPPADDING',    (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING',   (0,0), (-1,-1), 10),
            ('RIGHTPADDING',  (0,0), (-1,-1), 10),
            ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ]))
        elements.append(txn_table)
        elements.append(Spacer(1, 0.4*cm))

    # Participants list
    elements.append(Paragraph('Participants', h2_style))
    elements.append(Paragraph(
        '  .  '.join(trip['participants']),
        ParagraphStyle('parts', fontSize=9, textColor=BLACK, fontName='Helvetica', leading=14)
    ))
    elements.append(Spacer(1, 0.6*cm))

    # Footer
    elements.append(HRFlowable(width='100%', thickness=0.5, color=MGRAY))
    elements.append(Spacer(1, 0.2*cm))
    elements.append(Paragraph(
        f'Trip Expenses Calculator  .  Generated on {downloaded_at}',
        footer_style
    ))

    doc.build(elements)
    buf.seek(0)

    safe_name = trip['name'].replace(' ', '_').lower()
    filename = f'{safe_name}_{now.strftime("%Y%m%d_%H%M")}.pdf'
    return Response(
        buf,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)
