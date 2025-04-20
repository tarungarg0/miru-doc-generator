# Construct multiple line items rows
table_rows = ""
for item in items:
    amount = item["qty"] * item["rate"]
    table_rows += f"""
    <tr>
        <td>{item['hsn']}</td>
        <td>{item['desc']}</td>
        <td style='text-align:center;'>{item['qty']}</td>
        <td style='text-align:center;'>{item['unit']}</td>
        <td style='text-align:right;'>₹{item['rate']:,.2f}</td>
        <td style='text-align:right;'>₹{amount:,.2f}</td>
    </tr>
    """

html_filled = html_filled.replace("{{item_rows}}", table_rows)

html_filled = html_filled.replace("{{subtotal}}", f"{total:,.2f}")
html_filled = html_filled.replace("{{cgst}}", f"{total*0.09:,.2f}")
html_filled = html_filled.replace("{{sgst}}", f"{total*0.09:,.2f}")
html_filled = html_filled.replace("{{transportation}}", transport_included)
html_filled = html_filled.replace("{{total_amount}}", f"{grand_total:,.2f}")
html_filled = html_filled.replace("{{term_1}}", terms[0] if len(terms) > 0 else "")
html_filled = html_filled.replace("{{term_2}}", terms[1] if len(terms) > 1 else "")
html_filled = html_filled.replace("{{payment_terms}}", terms[2] if len(terms) > 2 else "")

# Show HTML preview
st.components.v1.html(html_filled, height=1000, scrolling=True)

# Generate PDF from HTML using WeasyPrint
try:
    pdf_bytes = HTML(string=html_filled).write_pdf()
    filename = f"{doc_type}_{client_name.replace(' ', '_')}.pdf"
    st.download_button("\ud83d\udcc5 Download PDF", data=pdf_bytes, file_name=filename)
except Exception as e:
    st.error(f"PDF generation failed: {e}")
