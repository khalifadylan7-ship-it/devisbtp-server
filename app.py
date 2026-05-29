from flask import Flask, request, jsonify, send_file, make_response
from flask_cors import CORS
import io, os, zipfile, datetime, xml.etree.ElementTree as ET, traceback, base64, requests

app = Flask(__name__)
CORS(app, origins="*")

FROM_EMAIL  = os.environ.get('FROM_EMAIL', '')
FROM_NAME   = os.environ.get('FROM_NAME', 'DevisBTP')
RESEND_KEY  = os.environ.get('RESEND_API_KEY', '')

def fmt_date(iso):
    try:
        return datetime.datetime.strptime(str(iso)[:10],'%Y-%m-%d').strftime('%d/%m/%Y')
    except:
        return str(iso or '')

def generate_xml(data):
    inv    = data.get('invoice', {})
    seller = data.get('seller', {})
    buyer  = data.get('buyer', {})
    lines  = data.get('lines', [])
    ht       = float(inv.get('ht',0) or 0)
    tva_rate = float(inv.get('tvaRate',0) or 0)
    tva_amt  = float(inv.get('tva',0) or 0)
    ttc      = float(inv.get('ttc',0) or 0)
    date_str = str(inv.get('date', datetime.date.today().isoformat()))[:10].replace('-','')
    ns = {
        'rsm':'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
        'ram':'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
        'udt':'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100',
    }
    for p,u in ns.items(): ET.register_namespace(p,u)
    def T(p,n): return f'{{{ns[p]}}}{n}'
    root = ET.Element(T('rsm','CrossIndustryInvoice'))
    ctx  = ET.SubElement(root,T('rsm','ExchangedDocumentContext'))
    gp   = ET.SubElement(ctx, T('ram','GuidelineSpecifiedDocumentContextParameter'))
    ET.SubElement(gp,T('ram','ID')).text = 'urn:factur-x.eu:1p0:en16931'
    doc  = ET.SubElement(root,T('rsm','ExchangedDocument'))
    ET.SubElement(doc,T('ram','ID')).text       = str(inv.get('num','FAC-001'))
    ET.SubElement(doc,T('ram','TypeCode')).text  = '380'
    iss  = ET.SubElement(doc,T('ram','IssueDateTime'))
    ET.SubElement(iss,T('udt','DateTimeString'),format='102').text = date_str
    tx   = ET.SubElement(root,T('rsm','SupplyChainTradeTransaction'))
    for i,line in enumerate(lines,1):
        li = ET.SubElement(tx,T('ram','IncludedSupplyChainTradeLineItem'))
        ld = ET.SubElement(li,T('ram','AssociatedDocumentLineDocument'))
        ET.SubElement(ld,T('ram','LineID')).text = str(i)
        sp = ET.SubElement(li,T('ram','SpecifiedTradeProduct'))
        ET.SubElement(sp,T('ram','Name')).text = str(line.get('desc',''))
        ag = ET.SubElement(li,T('ram','SpecifiedLineTradeAgreement'))
        np2= ET.SubElement(ag,T('ram','NetPriceProductTradePrice'))
        ET.SubElement(np2,T('ram','ChargeAmount')).text = f"{float(line.get('pu',0)):.2f}"
        dl = ET.SubElement(li,T('ram','SpecifiedLineTradeDelivery'))
        ET.SubElement(dl,T('ram','BilledQuantity'),unitCode='C62').text = str(line.get('qty',1))
        st = ET.SubElement(li,T('ram','SpecifiedLineTradeSettlement'))
        at = ET.SubElement(st,T('ram','ApplicableTradeTax'))
        ET.SubElement(at,T('ram','TypeCode')).text     = 'VAT'
        ET.SubElement(at,T('ram','CategoryCode')).text = 'S' if tva_rate>0 else 'Z'
        ET.SubElement(at,T('ram','RateApplicablePercent')).text = str(tva_rate)
        ms = ET.SubElement(st,T('ram','SpecifiedTradeSettlementLineMonetarySummation'))
        ET.SubElement(ms,T('ram','LineTotalAmount')).text = f"{float(line.get('total',0)):.2f}"
    ha = ET.SubElement(tx,T('ram','ApplicableHeaderTradeAgreement'))
    sr = ET.SubElement(ha,T('ram','SellerTradeParty'))
    ET.SubElement(sr,T('ram','Name')).text = str(seller.get('company') or seller.get('name',''))
    if seller.get('siret'):
        si = ET.SubElement(sr,T('ram','SpecifiedLegalOrganization'))
        ET.SubElement(si,T('ram','ID'),schemeID='0002').text = str(seller['siret']).replace(' ','')
    br = ET.SubElement(ha,T('ram','BuyerTradeParty'))
    ET.SubElement(br,T('ram','Name')).text = str(buyer.get('name',''))
    ET.SubElement(tx,T('ram','ApplicableHeaderTradeDelivery'))
    hs = ET.SubElement(tx,T('ram','ApplicableHeaderTradeSettlement'))
    ET.SubElement(hs,T('ram','InvoiceCurrencyCode')).text = 'EUR'
    tt2= ET.SubElement(hs,T('ram','ApplicableTradeTax'))
    ET.SubElement(tt2,T('ram','TypeCode')).text             = 'VAT'
    ET.SubElement(tt2,T('ram','BasisAmount')).text          = f"{ht:.2f}"
    ET.SubElement(tt2,T('ram','CalculatedAmount')).text     = f"{tva_amt:.2f}"
    ET.SubElement(tt2,T('ram','CategoryCode')).text         = 'S' if tva_rate>0 else 'Z'
    ET.SubElement(tt2,T('ram','RateApplicablePercent')).text= str(tva_rate)
    if tva_rate==0:
        ET.SubElement(tt2,T('ram','ExemptionReason')).text='TVA non applicable art. 293B CGI'
    ms2= ET.SubElement(hs,T('ram','SpecifiedTradeSettlementHeaderMonetarySummation'))
    ET.SubElement(ms2,T('ram','LineTotalAmount')).text                = f"{ht:.2f}"
    ET.SubElement(ms2,T('ram','TaxBasisTotalAmount')).text            = f"{ht:.2f}"
    ET.SubElement(ms2,T('ram','TaxTotalAmount'),currencyID='EUR').text= f"{tva_amt:.2f}"
    ET.SubElement(ms2,T('ram','GrandTotalAmount')).text               = f"{ttc:.2f}"
    ET.SubElement(ms2,T('ram','DuePayableAmount')).text               = f"{ttc:.2f}"
    return b'<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding='unicode').encode('utf-8')

def generate_html(data):
    inv        = data.get('invoice', {})
    seller     = data.get('seller', {})
    buyer      = data.get('buyer', {})
    lines      = data.get('lines', [])
    payment    = data.get('payment', '')
    signature  = data.get('signature', None)
    signer_name= data.get('signerName', '')
    signer_role= data.get('signerRole', '')
    signed_at  = data.get('signedAt', '')
    ht       = float(inv.get('ht',0) or 0)
    tva_rate = float(inv.get('tvaRate',0) or 0)
    tva_amt  = float(inv.get('tva',0) or 0)
    ttc      = float(inv.get('ttc',0) or 0)
    today    = datetime.date.today().strftime('%d/%m/%Y')
    seller_name  = str(seller.get('company') or seller.get('name') or '')
    seller_addr  = str(seller.get('address',''))
    seller_phone = str(seller.get('phone',''))
    seller_email = str(seller.get('email',''))
    seller_siret = str(seller.get('siret',''))
    seller_iban  = str(seller.get('iban',''))
    seller_bic   = str(seller.get('bic',''))
    rows = ''.join(f"<tr><td>{line.get('desc','')}</td><td style='text-align:center'>{line.get('qty','')}</td><td style='text-align:right'>{float(line.get('pu',0)):.2f} €</td><td style='text-align:right;font-weight:700'>{float(line.get('total',0)):.2f} €</td></tr>" for line in lines)
    tva_mention = '<p style="font-size:11px;color:#888;font-style:italic;margin:8px 0">TVA non applicable – article 293B du CGI</p>' if tva_rate == 0 else ''
    note_block  = f'<div style="background:#fff8f1;border-left:3px solid #f97316;padding:10px 14px;margin:12px 0;font-size:12px;color:#666;font-style:italic">{inv.get("note","")}</div>' if inv.get('note') else ''
    payment_rows = ''
    if payment: payment_rows += f'<tr><td style="color:#555;width:160px">Mode de règlement</td><td><strong>{payment}</strong></td></tr>'
    if seller_iban: payment_rows += f'<tr><td style="color:#555">IBAN</td><td>{seller_iban}</td></tr>'
    if seller_bic: payment_rows += f'<tr><td style="color:#555">BIC</td><td>{seller_bic}</td></tr>'
    if inv.get('num'): payment_rows += f'<tr><td style="color:#555">Référence</td><td>Merci de préciser le N° {inv["num"]}</td></tr>'
    payment_block = f'<div style="font-size:10px;font-weight:700;text-transform:uppercase;color:#888;margin:16px 0 8px">Informations de paiement</div><table style="width:100%;border-collapse:collapse;font-size:12px"><tbody>{payment_rows}</tbody></table>' if payment_rows else ''
    sig_block = ''
    if signature:
        sig_block = f'<div style="margin-top:20px"><div style="font-size:10px;font-weight:700;text-transform:uppercase;color:#888;margin-bottom:8px">Signature du client</div><img src="{signature}" style="max-width:220px;max-height:90px;border:1px solid #e5e7eb;border-radius:8px;padding:6px;background:white"><div style="font-size:11px;color:#555;margin-top:6px"><strong>{signer_name}</strong>{" – "+signer_role if signer_role else ""}<br>Signé le {fmt_date(signed_at[:10]) if signed_at else today}</div></div>'
    html = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8"><title>Facture {inv.get('num','')}</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:Arial,sans-serif;color:#111;padding:32px;font-size:13px;line-height:1.4}}.header{{display:flex;justify-content:space-between;margin-bottom:24px;gap:16px}}.co-name{{font-size:20px;font-weight:900;color:#f97316;margin-bottom:6px}}.co-info{{font-size:12px;color:#555;line-height:1.7}}.inv-block{{text-align:right}}.inv-num{{font-size:20px;font-weight:900;color:#f97316}}.inv-meta{{font-size:12px;color:#555;line-height:1.7}}.info-row{{display:flex;gap:16px;margin-bottom:20px}}.client-box{{background:#f3f4f6;padding:12px 16px;border-radius:8px;flex:1}}table.lines{{width:100%;border-collapse:collapse}}table.lines thead th{{background:#111318;color:white;padding:8px 10px;font-size:11px;text-align:left}}table.lines thead th:nth-child(2){{text-align:center}}table.lines thead th:nth-child(3),table.lines thead th:nth-child(4){{text-align:right}}table.lines tbody td{{padding:9px 10px;border-bottom:1px solid #f0f0f0}}.totals{{display:flex;flex-direction:column;align-items:flex-end;margin-top:12px}}.tot-row{{display:flex;justify-content:space-between;min-width:240px;font-size:13px;padding:3px 0;color:#555}}.tot-ttc{{font-size:17px;font-weight:900;color:#f97316;border-top:2px solid #f97316;padding-top:8px;margin-top:4px}}.footer{{margin-top:28px;border-top:1px solid #e5e7eb;padding-top:10px;font-size:10px;color:#aaa;text-align:center}}</style></head><body>
<div class="header"><div><div class="co-name">{seller_name}</div><div class="co-info">{seller_addr+'<br>' if seller_addr else ''}{'Tél : '+seller_phone+'<br>' if seller_phone else ''}{seller_email+'<br>' if seller_email else ''}{'SIRET : '+seller_siret if seller_siret else ''}</div></div>
<div class="inv-block"><div style="font-size:10px;font-weight:700;text-transform:uppercase;color:#888">Facture</div><div class="inv-num">{inv.get('num','')}</div><div class="inv-meta">Date : {fmt_date(inv.get('date',''))}<br>Échéance : {fmt_date(inv.get('dueDate','')) or '30 jours'}</div></div></div>
<div class="info-row"><div class="client-box"><div style="font-size:10px;font-weight:700;text-transform:uppercase;color:#888;margin-bottom:4px">Destinataire</div><div style="font-weight:700;font-size:14px">{buyer.get('name','')}</div>{'<div style="color:#555;font-size:12px">'+buyer.get('address','')+'</div>' if buyer.get('address') else ''}</div></div>
{f'<div style="margin-bottom:12px"><strong>Objet :</strong> {inv.get("objet","")}</div>' if inv.get('objet') else ''}
<table class="lines"><thead><tr><th>Description</th><th>Qté</th><th>P.U. HT</th><th>Total HT</th></tr></thead><tbody>{rows}</tbody></table>
<div class="totals"><div class="tot-row"><span>Sous-total HT</span><span>{ht:.2f} €</span></div><div class="tot-row"><span>TVA {tva_rate:.0f} %</span><span>{tva_amt:.2f} €</span></div><div class="tot-row tot-ttc"><span>TOTAL TTC</span><span>{ttc:.2f} €</span></div></div>
{note_block}{tva_mention}{payment_block}{sig_block}
<div class="footer">{seller_name}{' • SIRET '+seller_siret if seller_siret else ''}{' • '+seller_addr if seller_addr else ''}<br>Facture générée le {today} – Format Factur-X EN16931</div>
</body></html>"""
    return html.encode('utf-8')

def send_email_resend(to_email, to_name, subject, html_body, attachments):
    if not RESEND_KEY:
        raise ValueError("Variable d'environnement RESEND_API_KEY non configurée")
    if not FROM_EMAIL:
        raise ValueError("Variable d'environnement FROM_EMAIL non configurée")
    atts = []
    for filename, content in attachments:
        atts.append({
            'filename': filename,
            'content': base64.b64encode(content).decode('utf-8')
        })
    payload = {
        'from': f'{FROM_NAME} <{FROM_EMAIL}>',
        'to': [to_email],
        'subject': subject,
        'html': html_body,
        'attachments': atts
    }
    resp = requests.post(
        'https://api.resend.com/emails',
        headers={'Authorization': f'Bearer {RESEND_KEY}', 'Content-Type': 'application/json'},
        json=payload,
        timeout=20
    )
    if resp.status_code not in (200, 201):
        raise ValueError(f"Resend error {resp.status_code}: {resp.text}")

@app.route('/', methods=['GET'])
def health():
    return jsonify({'status':'ok','service':'DevisBTP Factur-X'})

@app.route('/generate', methods=['POST','OPTIONS'])
def generate():
    if request.method == 'OPTIONS':
        return make_response('',204)
    try:
        data      = request.get_json(force=True, silent=True) or {}
        xml_bytes = generate_xml(data)
        html_bytes= generate_html(data)
        inv_num   = str(data.get('invoice',{}).get('num','facture')).replace('/','_')
        zip_buf   = io.BytesIO()
        with zipfile.ZipFile(zip_buf,'w',zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f'{inv_num}_facture.html', html_bytes)
            zf.writestr(f'{inv_num}_facturx.xml',  xml_bytes)
        zip_buf.seek(0)
        return send_file(zip_buf, mimetype='application/zip', as_attachment=True, download_name=f'{inv_num}_facturx.zip')
    except Exception as e:
        return jsonify({'error':str(e),'trace':traceback.format_exc()}), 500

@app.route('/send-email', methods=['POST','OPTIONS'])
def send_email():
    if request.method == 'OPTIONS':
        return make_response('',204)
    try:
        data       = request.get_json(force=True, silent=True) or {}
        to_email   = data.get('to_email','')
        to_name    = data.get('to_name','')
        msg_type   = data.get('type','client')
        inv_data   = data.get('invoice_data', {})
        if not to_email:
            return jsonify({'error':'Email destinataire manquant'}), 400
        if not inv_data:
            return jsonify({'error':'Données facture manquantes (invoice_data)'}), 400
        if not inv_data.get('invoice'):
            return jsonify({'error':'Clé invoice manquante dans invoice_data'}), 400
        xml_bytes  = generate_xml(inv_data)
        html_bytes = generate_html(inv_data)
        inv_num    = str(inv_data.get('invoice',{}).get('num','facture')).replace('/','_')
        seller_name= str(inv_data.get('seller',{}).get('company') or inv_data.get('seller',{}).get('name',''))
        ttc        = float(inv_data.get('invoice',{}).get('ttc',0))
        if msg_type == 'accountant':
            subject   = f"Facture Factur-X {inv_num} – {seller_name}"
            html_body = f'<div style="font-family:Arial;max-width:600px;padding:20px"><h2 style="color:#f97316">{seller_name}</h2><p>Bonjour,</p><p>Veuillez trouver en pièce jointe la facture électronique <strong>{inv_num}</strong> au format Factur-X EN16931.</p><br><p>Cordialement,<br><strong>{seller_name}</strong></p></div>'
            attachments = [(f'{inv_num}_facturx.xml', xml_bytes), (f'{inv_num}_facture.html', html_bytes)]
        else:
            subject   = f"Votre facture {inv_num} – {seller_name}"
            html_body = f'<div style="font-family:Arial;max-width:600px;padding:20px"><h2 style="color:#f97316">{seller_name}</h2><p>Bonjour {to_name},</p><p>Veuillez trouver en pièce jointe votre facture <strong>{inv_num}</strong> d\'un montant de <strong>{ttc:.2f} €</strong> TTC.</p><br><p>Cordialement,<br><strong>{seller_name}</strong></p></div>'
            attachments = [(f'{inv_num}_facture.html', html_bytes), (f'{inv_num}_facturx.xml', xml_bytes)]
        send_email_resend(to_email, to_name, subject, html_body, attachments)
        return jsonify({'success': True, 'message': f'Email envoyé à {to_email}'})
    except Exception as e:
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

if __name__ == '__main__':
    app.run()
