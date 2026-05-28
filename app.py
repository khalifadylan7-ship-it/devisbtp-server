from flask import Flask, request, jsonify, send_file, make_response
from flask_cors import CORS
import io, os, zipfile, datetime, xml.etree.ElementTree as ET, traceback
 
app = Flask(__name__)
CORS(app, origins="*")
 
@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    r.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return r
 
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
    ET.SubElement(ms2,T('ram','LineTotalAmount')).text               = f"{ht:.2f}"
    ET.SubElement(ms2,T('ram','TaxBasisTotalAmount')).text           = f"{ht:.2f}"
    ET.SubElement(ms2,T('ram','TaxTotalAmount'),currencyID='EUR').text = f"{tva_amt:.2f}"
    ET.SubElement(ms2,T('ram','GrandTotalAmount')).text              = f"{ttc:.2f}"
    ET.SubElement(ms2,T('ram','DuePayableAmount')).text              = f"{ttc:.2f}"
    return b'<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding='unicode').encode('utf-8')
 
def generate_html_pdf(data):
    """Generate HTML invoice that can be printed as PDF"""
    inv    = data.get('invoice', {})
    seller = data.get('seller', {})
    buyer  = data.get('buyer', {})
    lines  = data.get('lines', [])
    ht       = float(inv.get('ht',0) or 0)
    tva_rate = float(inv.get('tvaRate',0) or 0)
    tva_amt  = float(inv.get('tva',0) or 0)
    ttc      = float(inv.get('ttc',0) or 0)
 
    rows = ''.join(f"""<tr>
        <td style="padding:8px;border-bottom:1px solid #eee">{line.get('desc','')}</td>
        <td style="padding:8px;border-bottom:1px solid #eee;text-align:center">{line.get('qty','')}</td>
        <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{float(line.get('pu',0)):.2f} €</td>
        <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;font-weight:600">{float(line.get('total',0)):.2f} €</td>
    </tr>""" for line in lines)
 
    tva_mention = 'TVA non applicable – article 293B du CGI' if tva_rate == 0 else ''
    note = str(inv.get('note',''))
    today = datetime.date.today().strftime('%d/%m/%Y')
 
    seller_name = str(seller.get('company') or seller.get('name') or '')
    seller_addr = str(seller.get('address',''))
    seller_phone= str(seller.get('phone',''))
    seller_email= str(seller.get('email',''))
    seller_siret= str(seller.get('siret',''))
 
    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<title>Facture {inv.get('num','')}</title>
<style>
  body{{font-family:Arial,sans-serif;color:#111;margin:0;padding:40px;font-size:13px}}
  .header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:30px}}
  .company-name{{font-size:22px;font-weight:bold;color:#f97316}}
  .invoice-title{{font-size:22px;font-weight:bold;text-align:right}}
  .invoice-num{{font-size:28px;font-weight:900;color:#f97316;text-align:right}}
  .info-block{{display:flex;justify-content:space-between;margin-bottom:20px;gap:20px}}
  .client-box{{background:#f3f4f6;padding:14px;border-radius:8px;flex:1}}
  .invoice-box{{background:#fff8f1;border:1px solid #fde8d0;padding:14px;border-radius:8px;min-width:200px}}
  table{{width:100%;border-collapse:collapse;margin:20px 0}}
  thead th{{background:#111318;color:white;padding:9px 8px;text-align:left;font-size:11px;text-transform:uppercase}}
  thead th:last-child,thead th:nth-child(3),thead th:nth-child(2){{text-align:right}}
  thead th:nth-child(2){{text-align:center}}
  .totals{{display:flex;flex-direction:column;align-items:flex-end;gap:4px;margin-top:10px}}
  .total-row{{display:flex;justify-content:space-between;min-width:260px;font-size:13px;padding:3px 0;color:#555}}
  .total-ttc{{font-size:16px;font-weight:900;color:#f97316;border-top:2px solid #f97316;padding-top:8px;margin-top:4px}}
  .footer{{margin-top:40px;border-top:1px solid #eee;padding-top:12px;font-size:11px;color:#888;text-align:center}}
  .note{{background:#fff8f1;border-left:3px solid #f97316;padding:10px 14px;margin:16px 0;font-size:12px;color:#666;font-style:italic}}
  .label{{font-size:10px;font-weight:bold;text-transform:uppercase;color:#888;margin-bottom:4px}}
</style></head><body>
<div class="header">
  <div>
    <div class="company-name">{seller_name}</div>
    <div style="color:#666;margin-top:6px;line-height:1.6;font-size:12px">
      {seller_addr}<br>
      {'Tél : '+seller_phone+'<br>' if seller_phone else ''}
      {seller_email+'<br>' if seller_email else ''}
      {'SIRET : '+seller_siret if seller_siret else ''}
    </div>
  </div>
  <div>
    <div class="invoice-title">FACTURE</div>
    <div class="invoice-num">{inv.get('num','')}</div>
    <div style="color:#888;font-size:12px;text-align:right;margin-top:4px">Date : {fmt_date(inv.get('date',''))}</div>
    <div style="color:#888;font-size:12px;text-align:right">Échéance : {fmt_date(inv.get('dueDate','')) or '30 jours'}</div>
  </div>
</div>
<div class="info-block">
  <div class="client-box">
    <div class="label">Destinataire</div>
    <div style="font-weight:bold;font-size:14px">{buyer.get('name','')}</div>
    <div style="color:#555;margin-top:2px">{buyer.get('address','')}</div>
  </div>
  <div class="invoice-box">
    <div class="label">Factur-X EN16931</div>
    <div style="font-size:11px;color:#888;margin-top:4px">Facture électronique<br>conforme au standard<br>européen EN16931</div>
  </div>
</div>
{f'<div style="margin-bottom:10px"><b>Objet :</b> {inv.get("objet","")}</div>' if inv.get('objet') else ''}
<table>
  <thead><tr><th>Description</th><th style="text-align:center">Qté</th><th style="text-align:right">P.U. HT</th><th style="text-align:right">Total HT</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
<div class="totals">
  <div class="total-row"><span>Sous-total HT</span><span>{ht:.2f} €</span></div>
  <div class="total-row"><span>TVA {tva_rate:.0f} %</span><span>{tva_amt:.2f} €</span></div>
  <div class="total-row total-ttc"><span>TOTAL TTC</span><span>{ttc:.2f} €</span></div>
</div>
{f'<div class="note">{note}</div>' if note else ''}
{f'<p style="font-size:11px;color:#888;font-style:italic">{tva_mention}</p>' if tva_mention else ''}
<div class="footer">
  {seller_name}{' • SIRET '+seller_siret if seller_siret else ''}{' • '+seller_addr if seller_addr else ''}<br>
  Facture générée le {today} – Format Factur-X EN16931
</div>
</body></html>"""
    return html.encode('utf-8')
 
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
        pdf_html  = generate_html_pdf(data)
        inv_num   = str(data.get('invoice',{}).get('num','facture')).replace('/','_')
        zip_buf   = io.BytesIO()
        with zipfile.ZipFile(zip_buf,'w',zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f'{inv_num}_facture.html', pdf_html)
            zf.writestr(f'{inv_num}_facturx.xml',  xml_bytes)
        zip_buf.seek(0)
        return send_file(zip_buf, mimetype='application/zip', as_attachment=True, download_name=f'{inv_num}_facturx.zip')
    except Exception as e:
        return jsonify({'error':str(e),'trace':traceback.format_exc()}), 500
 
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)))
