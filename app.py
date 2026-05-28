from flask import Flask, request, jsonify, send_file, make_response
from flask_cors import CORS
import io, os, zipfile, datetime, xml.etree.ElementTree as ET

app = Flask(__name__)
CORS(app, origins="*", allow_headers=["Content-Type", "Accept"], methods=["GET", "POST", "OPTIONS"])

def fmt_date(iso):
    try:
        d = datetime.datetime.strptime(str(iso)[:10], '%Y-%m-%d')
        return d.strftime('%d/%m/%Y')
    except:
        return str(iso) if iso else ''

def generate_xml(data):
    inv    = data.get('invoice', {})
    seller = data.get('seller', {})
    buyer  = data.get('buyer', {})
    lines  = data.get('lines', [])
    ht       = float(inv.get('ht', 0) or 0)
    tva_rate = float(inv.get('tvaRate', 0) or 0)
    tva_amt  = float(inv.get('tva', 0) or 0)
    ttc      = float(inv.get('ttc', 0) or 0)
    date_str = str(inv.get('date', datetime.date.today().isoformat()))[:10].replace('-','')
    due_str  = str(inv.get('dueDate', date_str))[:10].replace('-','') if inv.get('dueDate') else date_str

    ns_rsm = 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100'
    ns_ram = 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100'
    ns_udt = 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100'
    ET.register_namespace('rsm', ns_rsm)
    ET.register_namespace('ram', ns_ram)
    ET.register_namespace('udt', ns_udt)

    def R(prefix, name):
        ns = {'rsm':ns_rsm,'ram':ns_ram,'udt':ns_udt}
        return f'{{{ns[prefix]}}}{name}'

    root = ET.Element(R('rsm','CrossIndustryInvoice'))
    ctx  = ET.SubElement(root, R('rsm','ExchangedDocumentContext'))
    gp   = ET.SubElement(ctx,  R('ram','GuidelineSpecifiedDocumentContextParameter'))
    ET.SubElement(gp, R('ram','ID')).text = 'urn:factur-x.eu:1p0:en16931'

    doc = ET.SubElement(root, R('rsm','ExchangedDocument'))
    ET.SubElement(doc, R('ram','ID')).text      = str(inv.get('num','FAC-001'))
    ET.SubElement(doc, R('ram','TypeCode')).text = '380'
    iss = ET.SubElement(doc, R('ram','IssueDateTime'))
    ET.SubElement(iss, R('udt','DateTimeString'), format='102').text = date_str

    tx = ET.SubElement(root, R('rsm','SupplyChainTradeTransaction'))

    for i, line in enumerate(lines, 1):
        li = ET.SubElement(tx, R('ram','IncludedSupplyChainTradeLineItem'))
        ld = ET.SubElement(li, R('ram','AssociatedDocumentLineDocument'))
        ET.SubElement(ld, R('ram','LineID')).text = str(i)
        sp = ET.SubElement(li, R('ram','SpecifiedTradeProduct'))
        ET.SubElement(sp, R('ram','Name')).text = str(line.get('desc',''))
        ag = ET.SubElement(li, R('ram','SpecifiedLineTradeAgreement'))
        np = ET.SubElement(ag, R('ram','NetPriceProductTradePrice'))
        ET.SubElement(np, R('ram','ChargeAmount')).text = f"{float(line.get('pu',0)):.2f}"
        dl = ET.SubElement(li, R('ram','SpecifiedLineTradeDelivery'))
        ET.SubElement(dl, R('ram','BilledQuantity'), unitCode='C62').text = str(line.get('qty',1))
        st = ET.SubElement(li, R('ram','SpecifiedLineTradeSettlement'))
        at = ET.SubElement(st, R('ram','ApplicableTradeTax'))
        ET.SubElement(at, R('ram','TypeCode')).text     = 'VAT'
        ET.SubElement(at, R('ram','CategoryCode')).text = 'S' if tva_rate > 0 else 'Z'
        ET.SubElement(at, R('ram','RateApplicablePercent')).text = str(tva_rate)
        ms = ET.SubElement(st, R('ram','SpecifiedTradeSettlementLineMonetarySummation'))
        ET.SubElement(ms, R('ram','LineTotalAmount')).text = f"{float(line.get('total',0)):.2f}"

    ha = ET.SubElement(tx, R('ram','ApplicableHeaderTradeAgreement'))
    sr = ET.SubElement(ha, R('ram','SellerTradeParty'))
    ET.SubElement(sr, R('ram','Name')).text = str(seller.get('company') or seller.get('name',''))
    if seller.get('siret'):
        si = ET.SubElement(sr, R('ram','SpecifiedLegalOrganization'))
        ET.SubElement(si, R('ram','ID'), schemeID='0002').text = str(seller['siret']).replace(' ','')
    if seller.get('address'):
        sa = ET.SubElement(sr, R('ram','PostalTradeAddress'))
        ET.SubElement(sa, R('ram','LineOne')).text  = str(seller['address'])
        ET.SubElement(sa, R('ram','CountryID')).text = 'FR'

    br = ET.SubElement(ha, R('ram','BuyerTradeParty'))
    ET.SubElement(br, R('ram','Name')).text = str(buyer.get('name',''))
    if buyer.get('address'):
        ba = ET.SubElement(br, R('ram','PostalTradeAddress'))
        ET.SubElement(ba, R('ram','LineOne')).text   = str(buyer['address'])
        ET.SubElement(ba, R('ram','CountryID')).text  = 'FR'

    ET.SubElement(tx, R('ram','ApplicableHeaderTradeDelivery'))

    hs = ET.SubElement(tx, R('ram','ApplicableHeaderTradeSettlement'))
    ET.SubElement(hs, R('ram','InvoiceCurrencyCode')).text = 'EUR'
    tt = ET.SubElement(hs, R('ram','ApplicableTradeTax'))
    ET.SubElement(tt, R('ram','TypeCode')).text            = 'VAT'
    ET.SubElement(tt, R('ram','BasisAmount')).text         = f"{ht:.2f}"
    ET.SubElement(tt, R('ram','CalculatedAmount')).text    = f"{tva_amt:.2f}"
    ET.SubElement(tt, R('ram','CategoryCode')).text        = 'S' if tva_rate > 0 else 'Z'
    ET.SubElement(tt, R('ram','RateApplicablePercent')).text = str(tva_rate)
    if tva_rate == 0:
        ET.SubElement(tt, R('ram','ExemptionReason')).text = 'TVA non applicable – art. 293B du CGI'

    pt = ET.SubElement(hs, R('ram','SpecifiedTradePaymentTerms'))
    dd = ET.SubElement(pt, R('ram','DueDateDateTime'))
    ET.SubElement(dd, R('udt','DateTimeString'), format='102').text = due_str

    ms2 = ET.SubElement(hs, R('ram','SpecifiedTradeSettlementHeaderMonetarySummation'))
    ET.SubElement(ms2, R('ram','LineTotalAmount')).text                        = f"{ht:.2f}"
    ET.SubElement(ms2, R('ram','TaxBasisTotalAmount')).text                    = f"{ht:.2f}"
    ET.SubElement(ms2, R('ram','TaxTotalAmount'), currencyID='EUR').text       = f"{tva_amt:.2f}"
    ET.SubElement(ms2, R('ram','GrandTotalAmount')).text                       = f"{ttc:.2f}"
    ET.SubElement(ms2, R('ram','DuePayableAmount')).text                       = f"{ttc:.2f}"

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='unicode').encode('utf-8')

def generate_pdf(data):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor, white
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT, TA_CENTER

    inv    = data.get('invoice', {})
    seller = data.get('seller', {})
    buyer  = data.get('buyer', {})
    lines  = data.get('lines', [])
    ht       = float(inv.get('ht', 0) or 0)
    tva_rate = float(inv.get('tvaRate', 0) or 0)
    tva_amt  = float(inv.get('tva', 0) or 0)
    ttc      = float(inv.get('ttc', 0) or 0)

    ORANGE = HexColor('#f97316')
    DARK   = HexColor('#111318')
    GRAY   = HexColor('#6b7280')
    LIGHT  = HexColor('#f3f4f6')

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm, topMargin=18*mm, bottomMargin=18*mm)

    def S(name, **kw):
        return ParagraphStyle(name, **kw)

    s_title  = S('t',  fontName='Helvetica-Bold',   fontSize=18, textColor=ORANGE)
    s_body   = S('b',  fontName='Helvetica',         fontSize=9,  textColor=DARK,  leading=13)
    s_bold   = S('bo', fontName='Helvetica-Bold',    fontSize=9,  textColor=DARK)
    s_right  = S('r',  fontName='Helvetica',         fontSize=9,  textColor=DARK,  alignment=TA_RIGHT)
    s_rbold  = S('rb', fontName='Helvetica-Bold',    fontSize=9,  textColor=DARK,  alignment=TA_RIGHT)
    s_orange = S('o',  fontName='Helvetica-Bold',    fontSize=11, textColor=ORANGE,alignment=TA_RIGHT)
    s_label  = S('l',  fontName='Helvetica-Bold',    fontSize=7,  textColor=GRAY)
    s_note   = S('n',  fontName='Helvetica-Oblique', fontSize=8,  textColor=GRAY,  leading=12)
    s_center = S('c',  fontName='Helvetica',         fontSize=9,  textColor=DARK,  alignment=TA_CENTER)

    story = []

    seller_name = str(seller.get('company') or seller.get('name') or 'Mon Entreprise')
    h_data = [[Paragraph(seller_name, s_title), Paragraph('<b>FACTURE</b>', S('h2', fontName='Helvetica-Bold', fontSize=18, textColor=DARK, alignment=TA_RIGHT))]]
    h_tbl  = Table(h_data, colWidths=[95*mm, 75*mm])
    h_tbl.setStyle(TableStyle([('VALIGN','(0,0)','(-1,-1)','MIDDLE')]))
    story.append(h_tbl)
    story.append(Spacer(1, 4*mm))

    seller_lines = [l for l in [seller.get('address',''), seller.get('phone','') and f"Tél : {seller['phone']}", seller.get('email',''), seller.get('siret','') and f"SIRET : {seller['siret']}"] if l]
    inv_lines    = [f"<b>N° :</b> {inv.get('num','')}", f"<b>Date :</b> {fmt_date(inv.get('date',''))}", f"<b>Échéance :</b> {fmt_date(inv.get('dueDate','')) or '30 jours'}"]

    i_data = [[Paragraph('<br/>'.join(seller_lines), s_body), Paragraph('<br/>'.join(inv_lines), s_body)]]
    i_tbl  = Table(i_data, colWidths=[95*mm, 75*mm])
    i_tbl.setStyle(TableStyle([('VALIGN','(0,0)','(-1,-1)','TOP'), ('BACKGROUND','(1,0)','(1,0)', HexColor('#fff8f1')), ('BOX','(1,0)','(1,0)', 0.5, HexColor('#fde8d0')), ('TOPPADDING','(1,0)','(1,0)',8), ('BOTTOMPADDING','(1,0)','(1,0)',8), ('LEFTPADDING','(1,0)','(1,0)',8), ('RIGHTPADDING','(1,0)','(1,0)',8)]))
    story.append(i_tbl)
    story.append(Spacer(1, 5*mm))

    client_txt = f"<b>{buyer.get('name','')}</b>"
    if buyer.get('address'): client_txt += f"<br/>{buyer['address']}"
    c_tbl = Table([[Paragraph(client_txt, s_body)]], colWidths=[170*mm])
    c_tbl.setStyle(TableStyle([('BACKGROUND','(0,0)','(-1,-1)', LIGHT), ('TOPPADDING','(0,0)','(-1,-1)',8), ('BOTTOMPADDING','(0,0)','(-1,-1)',8), ('LEFTPADDING','(0,0)','(-1,-1)',10), ('RIGHTPADDING','(0,0)','(-1,-1)',10)]))
    story.append(Paragraph('DESTINATAIRE', s_label))
    story.append(Spacer(1,2*mm))
    story.append(c_tbl)
    story.append(Spacer(1, 5*mm))

    if inv.get('objet'):
        story.append(Paragraph(f"<b>Objet :</b> {inv['objet']}", s_body))
        story.append(Spacer(1, 3*mm))

    tbl_data = [[Paragraph('DESCRIPTION', s_label), Paragraph('QTÉ', S('ql', fontName='Helvetica-Bold', fontSize=7, textColor=GRAY, alignment=TA_CENTER)), Paragraph('P.U. HT', S('pl', fontName='Helvetica-Bold', fontSize=7, textColor=GRAY, alignment=TA_RIGHT)), Paragraph('TOTAL HT', S('tl', fontName='Helvetica-Bold', fontSize=7, textColor=GRAY, alignment=TA_RIGHT))]]
    for line in lines:
        total = float(line.get('qty', 0) or 0) * float(line.get('pu', 0) or 0)
        tbl_data.append([Paragraph(str(line.get('desc','')), s_body), Paragraph(str(line.get('qty','')), s_center), Paragraph(f"{float(line.get('pu',0)):.2f} €", s_right), Paragraph(f"{total:.2f} €", s_rbold)])

    l_tbl = Table(tbl_data, colWidths=[95*mm, 20*mm, 27*mm, 28*mm])
    ts = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), DARK), ('TEXTCOLOR', (0,0), (-1,0), white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,0), 7),
        ('TOPPADDING', (0,0), (-1,0), 7), ('BOTTOMPADDING', (0,0), (-1,0), 7),
        ('TOPPADDING', (0,1), (-1,-1), 7), ('BOTTOMPADDING', (0,1), (-1,-1), 7),
        ('LINEBELOW', (0,1), (-1,-1), 0.3, HexColor('#e5e7eb')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ])
    for i in range(2, len(tbl_data), 2):
        ts.add('BACKGROUND', (0,i), (-1,i), HexColor('#fafafa'))
    l_tbl.setStyle(ts)
    story.append(l_tbl)
    story.append(Spacer(1, 4*mm))

    tot_data = [
        [Paragraph('Sous-total HT', s_right), Paragraph(f"{ht:.2f} €", s_right)],
        [Paragraph(f'TVA {tva_rate:.0f} %' + (' (art. 293B CGI)' if tva_rate==0 else ''), s_right), Paragraph(f"{tva_amt:.2f} €", s_right)],
        [Paragraph('<b>TOTAL TTC</b>', s_orange), Paragraph(f'<b>{ttc:.2f} €</b>', s_orange)],
    ]
    tot_tbl = Table(tot_data, colWidths=[130*mm, 40*mm])
    tot_tbl.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LINEABOVE', (0,2), (-1,2), 1.5, ORANGE),
        ('BACKGROUND', (0,2), (-1,2), HexColor('#fff8f1')),
    ]))
    story.append(tot_tbl)

    if inv.get('note'):
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph(str(inv['note']), s_note))

    if tva_rate == 0:
        story.append(Spacer(1, 2*mm))
        story.append(Paragraph('TVA non applicable – article 293B du CGI', s_note))

    def footer(cv, d):
        cv.saveState()
        cv.setFont('Helvetica', 7)
        cv.setFillColorRGB(0.42, 0.44, 0.5)
        parts = [p for p in [seller.get('company',''), seller.get('siret','') and f"SIRET {seller['siret']}", seller.get('address','')] if p]
        cv.drawCentredString(A4[0]/2, 12*mm, ' • '.join(parts))
        cv.drawCentredString(A4[0]/2, 9*mm, f"Facture Factur-X EN16931 – {datetime.date.today().strftime('%d/%m/%Y')}")
        cv.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    return buf.read()

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Accept')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    return response

@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'DevisBTP Factur-X Server'})

@app.route('/generate', methods=['POST', 'OPTIONS'])
def generate():
    if request.method == 'OPTIONS':
        return make_response('', 204)
    try:
        data = request.get_json(force=True, silent=True) or {}
        pdf_bytes = generate_pdf(data)
        xml_bytes = generate_xml(data)
        inv_num   = str(data.get('invoice', {}).get('num', 'facture')).replace('/', '-')
        zip_buf   = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f'{inv_num}.pdf', pdf_bytes)
            zf.writestr(f'{inv_num}_facturx.xml', xml_bytes)
        zip_buf.seek(0)
        return send_file(zip_buf, mimetype='application/zip', as_attachment=True, download_name=f'{inv_num}_facturx.zip')
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
