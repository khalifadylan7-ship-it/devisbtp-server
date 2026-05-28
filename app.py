from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib import colors
import io, os, zipfile, datetime, xml.etree.ElementTree as ET

app = Flask(__name__)
CORS(app, origins="*", allow_headers=["Content-Type"], methods=["GET", "POST", "OPTIONS"])

# ── COULEURS ──
ORANGE  = HexColor('#f97316')
DARK    = HexColor('#111318')
GRAY    = HexColor('#6b7280')
LIGHT   = HexColor('#f9fafb')
WHITE   = white

def fmt_money(amount):
    return f"{amount:,.2f} €".replace(',', ' ').replace('.', ',')

def fmt_date(iso):
    try:
        d = datetime.datetime.strptime(iso[:10], '%Y-%m-%d')
        return d.strftime('%d/%m/%Y')
    except:
        return iso or ''

# ══════════════════════════════════════════
#  GÉNÉRATION XML FACTUR-X
# ══════════════════════════════════════════
def generate_facturx_xml(data):
    inv = data.get('invoice', {})
    seller = data.get('seller', {})
    buyer  = data.get('buyer',  {})
    lines  = data.get('lines',  [])

    ht       = float(inv.get('ht',  0))
    tva_rate = float(inv.get('tvaRate', 0))
    tva_amt  = float(inv.get('tva', 0))
    ttc      = float(inv.get('ttc', 0))
    date_str = inv.get('date', datetime.date.today().isoformat())[:10].replace('-', '')
    due_str  = inv.get('dueDate', date_str)[:10].replace('-', '') if inv.get('dueDate') else date_str

    ns = {
        'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
        'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
        'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100',
        'qdt': 'urn:un:unece:uncefact:data:standard:QualifiedDataType:100',
    }
    for prefix, uri in ns.items():
        ET.register_namespace(prefix, uri)

    def tag(prefix, name):
        return f'{{{ns[prefix]}}}{name}'

    root = ET.Element(tag('rsm','CrossIndustryInvoice'))

    # ExchangedDocumentContext
    ctx = ET.SubElement(root, tag('rsm','ExchangedDocumentContext'))
    gp  = ET.SubElement(ctx,  tag('ram','GuidelineSpecifiedDocumentContextParameter'))
    gid = ET.SubElement(gp,   tag('ram','ID'))
    gid.text = 'urn:factur-x.eu:1p0:en16931'

    # ExchangedDocument
    doc = ET.SubElement(root, tag('rsm','ExchangedDocument'))
    did = ET.SubElement(doc,  tag('ram','ID'));      did.text = inv.get('num', 'FAC-001')
    tc  = ET.SubElement(doc,  tag('ram','TypeCode'));tc.text  = '380'  # Commercial invoice
    iss = ET.SubElement(doc,  tag('ram','IssueDateTime'))
    dts = ET.SubElement(iss,  tag('udt','DateTimeString'), format='102'); dts.text = date_str

    # SupplyChainTradeTransaction
    tx = ET.SubElement(root, tag('rsm','SupplyChainTradeTransaction'))

    # Line items
    for i, line in enumerate(lines, 1):
        li = ET.SubElement(tx, tag('ram','IncludedSupplyChainTradeLineItem'))
        ld = ET.SubElement(li, tag('ram','AssociatedDocumentLineDocument'))
        ln = ET.SubElement(ld, tag('ram','LineID')); ln.text = str(i)
        sp = ET.SubElement(li, tag('ram','SpecifiedTradeProduct'))
        nm = ET.SubElement(sp, tag('ram','Name')); nm.text = line.get('desc', '')
        ag = ET.SubElement(li, tag('ram','SpecifiedLineTradeAgreement'))
        np = ET.SubElement(ag, tag('ram','NetPriceProductTradePrice'))
        ca = ET.SubElement(np, tag('ram','ChargeAmount')); ca.text = f"{float(line.get('pu',0)):.2f}"
        dl = ET.SubElement(li, tag('ram','SpecifiedLineTradeDelivery'))
        bq = ET.SubElement(dl, tag('ram','BilledQuantity'), unitCode='C62'); bq.text = str(line.get('qty',1))
        st = ET.SubElement(li, tag('ram','SpecifiedLineTradeSettlement'))
        at = ET.SubElement(st, tag('ram','ApplicableTradeTax'))
        tc2= ET.SubElement(at, tag('ram','TypeCode'));    tc2.text = 'VAT'
        cc = ET.SubElement(at, tag('ram','CategoryCode'));cc.text  = 'S' if tva_rate > 0 else 'Z'
        rp = ET.SubElement(at, tag('ram','RateApplicablePercent')); rp.text = str(tva_rate)
        ms = ET.SubElement(st, tag('ram','SpecifiedTradeSettlementLineMonetarySummation'))
        la = ET.SubElement(ms, tag('ram','LineTotalAmount')); la.text = f"{float(line.get('total',0)):.2f}"

    # Header trade agreement
    ha = ET.SubElement(tx, tag('ram','ApplicableHeaderTradeAgreement'))
    sr = ET.SubElement(ha, tag('ram','SellerTradeParty'))
    sn = ET.SubElement(sr, tag('ram','Name')); sn.text = seller.get('company', seller.get('name',''))
    if seller.get('siret'):
        si = ET.SubElement(sr, tag('ram','SpecifiedLegalOrganization'))
        si_id = ET.SubElement(si, tag('ram','ID'), schemeID='0002'); si_id.text = seller.get('siret','').replace(' ','')
    if seller.get('address'):
        sa = ET.SubElement(sr, tag('ram','PostalTradeAddress'))
        sl = ET.SubElement(sa, tag('ram','LineOne')); sl.text = seller.get('address','')
        sc = ET.SubElement(sa, tag('ram','CountryID')); sc.text = 'FR'
    if seller.get('email'):
        sc2 = ET.SubElement(sr, tag('ram','URIUniversalCommunication'))
        su  = ET.SubElement(sc2,tag('ram','URIID'), schemeID='EM'); su.text = seller.get('email','')

    br = ET.SubElement(ha, tag('ram','BuyerTradeParty'))
    bn = ET.SubElement(br, tag('ram','Name')); bn.text = buyer.get('name','')
    if buyer.get('address'):
        ba = ET.SubElement(br, tag('ram','PostalTradeAddress'))
        bl = ET.SubElement(ba, tag('ram','LineOne')); bl.text = buyer.get('address','')
        bco= ET.SubElement(ba, tag('ram','CountryID')); bco.text = 'FR'

    # Delivery
    hd = ET.SubElement(tx, tag('ram','ApplicableHeaderTradeDelivery'))

    # Settlement
    hs = ET.SubElement(tx, tag('ram','ApplicableHeaderTradeSettlement'))
    cc2= ET.SubElement(hs, tag('ram','InvoiceCurrencyCode')); cc2.text = 'EUR'
    tt = ET.SubElement(hs, tag('ram','ApplicableTradeTax'))
    tt_type = ET.SubElement(tt, tag('ram','TypeCode'));    tt_type.text = 'VAT'
    tt_base = ET.SubElement(tt, tag('ram','BasisAmount')); tt_base.text = f"{ht:.2f}"
    tt_calc = ET.SubElement(tt, tag('ram','CalculatedAmount')); tt_calc.text = f"{tva_amt:.2f}"
    tt_cat  = ET.SubElement(tt, tag('ram','CategoryCode')); tt_cat.text  = 'S' if tva_rate > 0 else 'Z'
    tt_rate = ET.SubElement(tt, tag('ram','RateApplicablePercent')); tt_rate.text = str(tva_rate)

    if tva_rate == 0:
        tt_ex = ET.SubElement(tt, tag('ram','ExemptionReasonCode')); tt_ex.text = 'VATEX-EU-AE'
        tt_er = ET.SubElement(tt, tag('ram','ExemptionReason')); tt_er.text = 'TVA non applicable – art. 293B du CGI'

    # Due date
    pt = ET.SubElement(hs, tag('ram','SpecifiedTradePaymentTerms'))
    dd = ET.SubElement(pt, tag('ram','DueDateDateTime'))
    ddt= ET.SubElement(dd, tag('udt','DateTimeString'), format='102'); ddt.text = due_str

    # Totals
    ms2 = ET.SubElement(hs, tag('ram','SpecifiedTradeSettlementHeaderMonetarySummation'))
    lta = ET.SubElement(ms2,tag('ram','LineTotalAmount'));        lta.text = f"{ht:.2f}"
    tta = ET.SubElement(ms2,tag('ram','TaxBasisTotalAmount'));    tta.text = f"{ht:.2f}"
    txa = ET.SubElement(ms2,tag('ram','TaxTotalAmount'), currencyID='EUR'); txa.text = f"{tva_amt:.2f}"
    gta = ET.SubElement(ms2,tag('ram','GrandTotalAmount'));       gta.text = f"{ttc:.2f}"
    dpa = ET.SubElement(ms2,tag('ram','DuePayableAmount'));       dpa.text = f"{ttc:.2f}"

    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='unicode').encode('utf-8')
    return xml_bytes

# ══════════════════════════════════════════
#  GÉNÉRATION PDF
# ══════════════════════════════════════════
def generate_pdf(data):
    inv    = data.get('invoice', {})
    seller = data.get('seller', {})
    buyer  = data.get('buyer',  {})
    lines  = data.get('lines',  [])

    ht       = float(inv.get('ht',  0))
    tva_rate = float(inv.get('tvaRate', 0))
    tva_amt  = float(inv.get('tva', 0))
    ttc      = float(inv.get('ttc', 0))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=18*mm,  bottomMargin=18*mm)

    styles = getSampleStyleSheet()
    story  = []

    def style(name, **kw):
        s = ParagraphStyle(name, **kw)
        return s

    S_title  = style('title',  fontName='Helvetica-Bold',   fontSize=22, textColor=ORANGE, spaceAfter=2)
    S_sub    = style('sub',    fontName='Helvetica',         fontSize=8,  textColor=GRAY,   spaceAfter=1)
    S_body   = style('body',   fontName='Helvetica',         fontSize=9,  textColor=DARK,   leading=13)
    S_bold   = style('bold',   fontName='Helvetica-Bold',    fontSize=9,  textColor=DARK)
    S_right  = style('right',  fontName='Helvetica',         fontSize=9,  textColor=DARK,   alignment=TA_RIGHT)
    S_rbold  = style('rbold',  fontName='Helvetica-Bold',    fontSize=9,  textColor=DARK,   alignment=TA_RIGHT)
    S_orange = style('orange', fontName='Helvetica-Bold',    fontSize=11, textColor=ORANGE, alignment=TA_RIGHT)
    S_label  = style('label',  fontName='Helvetica-Bold',    fontSize=7,  textColor=GRAY,   spaceAfter=2)
    S_note   = style('note',   fontName='Helvetica-Oblique', fontSize=8,  textColor=GRAY,   leading=12)

    # ── HEADER ──
    seller_name = seller.get('company') or seller.get('name','Mon Entreprise')
    header_data = [[
        Paragraph(f'<font color="#f97316">{seller_name}</font>', style('h1', fontName='Helvetica-Bold', fontSize=18, textColor=ORANGE)),
        Paragraph(f'<b>FACTURE</b>', style('h2', fontName='Helvetica-Bold', fontSize=18, textColor=DARK, alignment=TA_RIGHT))
    ]]
    header_tbl = Table(header_data, colWidths=[95*mm, 75*mm])
    header_tbl.setStyle(TableStyle([('VALIGN','(0,0)','(-1,-1)','MIDDLE')]))
    story.append(header_tbl)
    story.append(Spacer(1, 4*mm))

    # ── INFO SELLER / INVOICE ──
    seller_lines = []
    if seller.get('address'): seller_lines.append(seller['address'])
    if seller.get('phone'):   seller_lines.append(f"Tél : {seller['phone']}")
    if seller.get('email'):   seller_lines.append(seller['email'])
    if seller.get('siret'):   seller_lines.append(f"SIRET : {seller['siret']}")
    seller_txt = Paragraph('<br/>'.join(seller_lines), S_body)

    due = inv.get('dueDate')
    inv_lines = [
        f"<b>N° :</b> {inv.get('num','FAC-001')}",
        f"<b>Date :</b> {fmt_date(inv.get('date',''))}",
        f"<b>Échéance :</b> {fmt_date(due) if due else '30 jours'}",
    ]
    inv_txt = Paragraph('<br/>'.join(inv_lines), S_body)

    info_data = [[seller_txt, inv_txt]]
    info_tbl  = Table(info_data, colWidths=[95*mm, 75*mm])
    info_tbl.setStyle(TableStyle([
        ('VALIGN','(0,0)','(-1,-1)','TOP'),
        ('BACKGROUND','(1,0)','(1,0)', HexColor('#fff8f1')),
        ('BOX','(1,0)','(1,0)', 0.5, HexColor('#fde8d0')),
        ('ROUNDEDCORNERS', [6]),
        ('PADDING','(1,0)','(1,0)', 8),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 5*mm))

    # ── CLIENT ──
    client_label = Paragraph('DESTINATAIRE', S_label)
    client_name  = buyer.get('name','')
    client_addr  = buyer.get('address','')
    client_block = Paragraph(f"<b>{client_name}</b><br/>{client_addr}", S_body)

    client_data = [['', ''], [client_label, ''], [client_block, '']]
    client_tbl  = Table([[client_block]], colWidths=[170*mm])
    client_tbl.setStyle(TableStyle([
        ('BACKGROUND','(0,0)','(-1,-1)', HexColor('#f3f4f6')),
        ('BOX','(0,0)','(-1,-1)', 0, white),
        ('LEFTPADDING','(0,0)','(-1,-1)', 10),
        ('RIGHTPADDING','(0,0)','(-1,-1)', 10),
        ('TOPPADDING','(0,0)','(-1,-1)', 8),
        ('BOTTOMPADDING','(0,0)','(-1,-1)', 8),
        ('ROUNDEDCORNERS', [8]),
    ]))
    story.append(Paragraph('DESTINATAIRE', S_label))
    story.append(client_tbl)
    story.append(Spacer(1, 5*mm))

    # Objet
    if inv.get('objet'):
        story.append(Paragraph(f"<b>Objet :</b> {inv['objet']}", S_body))
        story.append(Spacer(1, 3*mm))

    # ── TABLEAU LIGNES ──
    tbl_data = [[
        Paragraph('DESCRIPTION', S_label),
        Paragraph('QTÉ',         style('cl', fontName='Helvetica-Bold', fontSize=7, textColor=GRAY, alignment=TA_CENTER)),
        Paragraph('P.U. HT',     style('rl', fontName='Helvetica-Bold', fontSize=7, textColor=GRAY, alignment=TA_RIGHT)),
        Paragraph('TOTAL HT',    style('rl2',fontName='Helvetica-Bold', fontSize=7, textColor=GRAY, alignment=TA_RIGHT)),
    ]]
    for line in lines:
        total = float(line.get('qty',0)) * float(line.get('pu',0))
        tbl_data.append([
            Paragraph(str(line.get('desc','')), S_body),
            Paragraph(str(line.get('qty','')),  style('qc', fontName='Helvetica', fontSize=9, textColor=DARK, alignment=TA_CENTER)),
            Paragraph(f"{float(line.get('pu',0)):.2f} €", S_right),
            Paragraph(f"{total:.2f} €", S_rbold),
        ])

    lines_tbl = Table(tbl_data, colWidths=[95*mm, 20*mm, 27*mm, 28*mm])
    ts = TableStyle([
        # Header
        ('BACKGROUND',    (0,0), (-1,0),  HexColor('#111318')),
        ('TEXTCOLOR',     (0,0), (-1,0),  white),
        ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,0),  7),
        ('TOPPADDING',    (0,0), (-1,0),  7),
        ('BOTTOMPADDING', (0,0), (-1,0),  7),
        # Rows
        ('FONTNAME',      (0,1), (-1,-1), 'Helvetica'),
        ('FONTSIZE',      (0,1), (-1,-1), 9),
        ('TOPPADDING',    (0,1), (-1,-1), 7),
        ('BOTTOMPADDING', (0,1), (-1,-1), 7),
        ('LINEBELOW',     (0,1), (-1,-1), 0.3, HexColor('#e5e7eb')),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        # Alternating
        *[('BACKGROUND', (0,i), (-1,i), HexColor('#fafafa')) for i in range(2, len(tbl_data), 2)],
    ])
    lines_tbl.setStyle(ts)
    story.append(lines_tbl)
    story.append(Spacer(1, 4*mm))

    # ── TOTAUX ──
    totaux_data = [
        [Paragraph('Sous-total HT', S_right), Paragraph(f"{ht:.2f} €", S_right)],
        [Paragraph(f'TVA {tva_rate:.0f} %', S_right), Paragraph(f"{tva_amt:.2f} €", S_right)],
        [Paragraph('<b>TOTAL TTC</b>', S_orange), Paragraph(f'<b>{ttc:.2f} €</b>', S_orange)],
    ]
    if tva_rate == 0:
        totaux_data[1] = [Paragraph('TVA 0 % (art. 293B CGI)', S_right), Paragraph('0,00 €', S_right)]

    totaux_tbl = Table(totaux_data, colWidths=[130*mm, 40*mm])
    totaux_tbl.setStyle(TableStyle([
        ('ALIGN',         (0,0), (-1,-1), 'RIGHT'),
        ('TOPPADDING',    (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LINEABOVE',     (0,2), (-1,2),  1.5, ORANGE),
        ('BACKGROUND',    (0,2), (-1,2),  HexColor('#fff8f1')),
    ]))
    story.append(totaux_tbl)
    story.append(Spacer(1, 4*mm))

    # ── NOTE ──
    if inv.get('note'):
        story.append(Paragraph(f"<i>{inv['note']}</i>", S_note))
        story.append(Spacer(1, 3*mm))

    # TVA non applicable mention
    if tva_rate == 0:
        story.append(Paragraph('TVA non applicable – article 293B du CGI', S_note))
        story.append(Spacer(1, 2*mm))

    # Signature
    if data.get('signature'):
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph('ACCEPTÉ ET SIGNÉ PAR LE CLIENT', S_label))
        sig_info = f"<b>{data.get('signerName','')}</b>"
        if data.get('signerRole'): sig_info += f" – {data['signerRole']}"
        if data.get('signedAt'):   sig_info += f"<br/>Signé le {fmt_date(data['signedAt'])}"
        story.append(Paragraph(sig_info, S_body))

    # ── FOOTER ──
    def footer(canvas_obj, doc_obj):
        canvas_obj.saveState()
        canvas_obj.setFont('Helvetica', 7)
        canvas_obj.setFillColor(GRAY)
        footer_parts = []
        if seller.get('company'): footer_parts.append(seller['company'])
        if seller.get('siret'):   footer_parts.append(f"SIRET {seller['siret']}")
        if seller.get('address'): footer_parts.append(seller['address'])
        footer_txt = ' • '.join(footer_parts)
        canvas_obj.drawCentredString(A4[0]/2, 12*mm, footer_txt)
        canvas_obj.drawCentredString(A4[0]/2, 9*mm, f"Facture générée le {datetime.date.today().strftime('%d/%m/%Y')} – Format Factur-X EN16931")
        canvas_obj.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    return buf.read()

# ══════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════
@app.route('/', methods=['GET'])
def health():
    return jsonify({ 'status': 'ok', 'service': 'DevisBTP Factur-X Server' })

@app.route('/generate', methods=['POST', 'OPTIONS'])
def generate():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({'error': 'Données manquantes'}), 400

        pdf_bytes = generate_pdf(data)
        xml_bytes = generate_facturx_xml(data)

        # ZIP contenant PDF + XML
        zip_buf = io.BytesIO()
        inv_num = data.get('invoice', {}).get('num', 'facture').replace('/', '-')
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f'{inv_num}.pdf', pdf_bytes)
            zf.writestr(f'{inv_num}_facturx.xml', xml_bytes)
        zip_buf.seek(0)

        return send_file(
            zip_buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'{inv_num}_facturx.zip'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/pdf', methods=['POST'])
def pdf_only():
    try:
        data = request.get_json(force=True)
        pdf_bytes = generate_pdf(data)
        buf = io.BytesIO(pdf_bytes)
        inv_num = data.get('invoice', {}).get('num', 'facture').replace('/', '-')
        return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'{inv_num}.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/xml', methods=['POST'])
def xml_only():
    try:
        data = request.get_json(force=True)
        xml_bytes = generate_facturx_xml(data)
        buf = io.BytesIO(xml_bytes)
        inv_num = data.get('invoice', {}).get('num', 'facture').replace('/', '-')
        return send_file(buf, mimetype='application/xml', as_attachment=True, download_name=f'{inv_num}_facturx.xml')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
