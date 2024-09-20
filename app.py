import os
from PyPDF2 import PdfReader, PdfWriter
import streamlit as st
import zipfile
from io import BytesIO
import re
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer, LTChar
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image

# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe' #to remove when pushing


KEYWORDS_LIST = [
    # Invoice Identifiers
    "Tax Invoice", "Proforma Invoice", "Commercial Invoice", "Credit Invoice",
    "Debit Invoice", "VAT Invoice", "GST Invoice", "Sales Invoice", "Service Invoice",
    "Invoice No", "Invoice Number", "Invoice #", "Invoice ID", "Bill No", "Bill Number", "Bill ID", "Inv No"

    # Purchase Order References
    "PO", "PO No", "PO Number", "Purchase Order", "Purchase Order No", "Purchase Order Number",
    "Order", "Order No", "Order Number", "Sales Order",

    # Date Indicators
    "Invoice Date", "Date of Issue", "Issue Date", "Billing Date", "Date", "Transaction Date", "Bill Date", "Due Date"

    # Payment Terms and Conditions
    "Terms", "Payment Terms", "Delivery Terms", "Payment Due", "Due Date", "Net Terms", "Terms and Conditions",

    # Customer and Supplier Information
    "Bill To", "Billed To", "Invoice To", "Invoiced To", "Sold To", "Customer", "Client", "Buyer",
    "Ship To", "Shipping Address", "Delivery Address", "Deliver To", "Consignee", "Billing Address", "Bill From"

    # Financial and Tax Information
    "Tax ID", "VAT Number", "GST Number", "Tax Registration Number", "Company Registration Number",
    "Taxable Amount", "Tax Invoice",

    # Amount and Payment Details
    "Amount Due", "Total Due", "Balance Due", "Total Amount", "Grand Total", "Subtotal",
    "Payment Instructions", "Remittance Advice", "Amount Paid",

    # Itemization Headers
    "Description", "Item", "Quantity", "Unit Price", "Price", "Amount", "Line Total", "Product Code", "SKU",

    # Contact and Company Information
    "Contact", "Contact Details", "Phone", "Email", "Fax", "Website", "Customer Service", "Support",

    # Legal and Compliance Notices
    "Authorized Signature", "Signature", "Terms of Service", "Privacy Policy", "Disclaimer", "Confidential",

    # Other Relevant Keywords
    "Account Summary", "Statement", "Billing Statement", "Reference", "Reference No", "Reference Number",
    "Transaction ID", "Customer ID", "Client ID", "Account Number", "Payment Method"
]

LOGIC_TYPE = "OR"

# Function to extract text using OCR for scanned PDFs
def ocr_extract_text_from_page(reader, page_num):
    # Extract the specific page as a new single-page PDF
    pdf_writer = PdfWriter()
    pdf_writer.add_page(reader.pages[page_num])

    # Create a byte stream for the single-page PDF
    pdf_bytes_io = BytesIO()
    pdf_writer.write(pdf_bytes_io)
    pdf_bytes_io.seek(0)

    # Convert the specific page (now a byte stream) to an image for OCR processing
    image = convert_from_bytes(pdf_bytes_io.read(), first_page=1, last_page=1)[0] #convert from bytes returns list, take first element

    # Convert the PDF page to an image
    if image:
        text = pytesseract.image_to_string(image)
        return text.lower()  # Convert everything to lowercase for checking
    return None


# Function to check if keyword appears in the top 1/4 of the image for scanned PDFs
def ocr_keyword_in_top_quarter(image, keywords):
    ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    image_height = image.size[1]  # Height of the image

    current_line_num = None
    line_text = ""

    # Iterate over all detected words in the image
    for i, word in enumerate(ocr_data["text"]):
        if not word.strip():  # Skip empty or whitespace-only words
            continue

        # If it's a new line, check the previous line
        if ocr_data["line_num"][i] != current_line_num:
            if line_text:
                # Check the top position of the first word in the previous line
                word_top = int(ocr_data["top"][i - 1])  # Top position of the previous line
                if word_top < (image_height / 4):  # Check if the line is in the top 1/4
                    for keyword in keywords:
                        if keyword.lower() in line_text.lower():
                            return True  # Keyword found in this line
            # Start a new line
            current_line_num = ocr_data["line_num"][i]
            line_text = word  # Reset line text with the current word
        else:
            # Same line, so keep appending words
            line_text += " " + word

    # Check the last line after the loop finishes
    if line_text:
        word_top = int(ocr_data["top"][-1])  # Top position of the last word
        if word_top < (image_height / 4):  # Check if it's in the top quarter
            for keyword in keywords:
                if keyword.lower() in line_text.lower():
                    return True  # Keyword found in the last line

    return False


# Function to extract text from each page of the PDF (digital PDF)
def extract_text_from_page(reader, page_num):
    try:
        page = reader.pages[page_num]
        return page.extract_text().lower()  # Convert to lowercase for checking
    except Exception as e:
        st.warning(f"Warning: Could not extract text from page {page_num + 1}: {e}")
        return None


# Function to check if a keyword appears in the top 1/4 of the page for digital PDFs
def keyword_in_top_quarter(pdf_file, page_num, keywords):
    for page_layout in extract_pages(pdf_file, page_numbers=[page_num]):
        page_height = page_layout.height

        for element in page_layout:
            if isinstance(element, LTTextContainer):
                for text_line in element:
                    text = text_line.get_text()
                    if any(keyword.lower() in text.lower() for keyword in keywords):
                        # Get the vertical position of the text line
                        y = text_line.bbox[1]  # Get the y-coordinate (bottom) of the text line
                        if y > (page_height * 3 / 4):  # Top 1/4 of the page
                            return True
    return False


# Function to detect continuation pages based on content
def is_continuation_page(text):
    continuation_keywords = ["Continued"]
    if any(keyword.lower() in text.lower() for keyword in continuation_keywords):
        return True

    page_pattern = r"page\s+(\d+)\s+of\s+(\d+)"
    match = re.search(page_pattern, text)
    if match:
        current_page = int(match.group(1))
        total_pages = int(match.group(2))
        if current_page > 1 and total_pages > 1:
            return True
    return False


# Function to split PDFs based on keywords
def split_invoices_by_keywords(pdf_file, keywords, logic_type="OR"):
    reader = PdfReader(pdf_file)
    original_pdf_name = os.path.splitext(os.path.basename(pdf_file.name))[0]

    invoice_count = 0
    writer = None
    output_files = []
    start_page = 0

    for page_num in range(len(reader.pages)):
        page_text = extract_text_from_page(reader, page_num)
        ocr_flag = False  # Flag to check if OCR was used

        # Try OCR if text extraction from digital PDF fails
        if page_text is None or page_text.strip() == "":
            page_text = ocr_extract_text_from_page(reader, page_num)
            ocr_flag = True

        print(f"Processing page {page_num + 1}")
        print(f"Extracted text: {page_text if page_text else 'No text extracted'}") #get first 100 char
        print(f"OCR used: {ocr_flag}")

        # If both text extraction and OCR fail, mark the page as unreadable and split
        if page_text is None:
            print("Text extraction and OCR failed")
            if writer is not None:
                invoice_file = f"{original_pdf_name}_start-page-{start_page + 1}.pdf"
                with open(invoice_file, "wb") as f:
                    writer.write(f)
                output_files.append(invoice_file)
                invoice_count += 1
                writer = None

            # Create a new writer for the unreadable page
            writer = PdfWriter()
            writer.add_page(reader.pages[page_num])
            invoice_file = f"{original_pdf_name}_start-page-{page_num + 1}_unreadable.pdf"
            with open(invoice_file, "wb") as f:
                writer.write(f)
            output_files.append(invoice_file)
            invoice_count += 1
            writer = None
            continue

        # Detect continuation pages
        if is_continuation_page(page_text):
            print("Continuation page detected")
            if writer is not None:
                writer.add_page(reader.pages[page_num])  # Add to the current writer instance
            continue

        # Check keywords for scanned PDFs (OCR-based)
        if ocr_flag and page_text:
            # Convert the page to an image and check for keywords in the top quarter
            pdf_writer = PdfWriter()
            pdf_writer.add_page(reader.pages[page_num])

            # Create a byte stream for the single-page PDF
            pdf_bytes_io = BytesIO()
            pdf_writer.write(pdf_bytes_io)
            pdf_bytes_io.seek(0)

            # Convert the page to an image for OCR processing
            image = convert_from_bytes(pdf_bytes_io.read(), first_page=1, last_page=1)[0]
            print(ocr_keyword_in_top_quarter(image, keywords))
            if ocr_keyword_in_top_quarter(image, keywords):
                print("Keyword found in top quarter (OCR-based PDF)")
                if writer is not None:
                    # Save the current invoice before starting a new one
                    invoice_file = f"{original_pdf_name}_start-page-{start_page + 1}.pdf"
                    with open(invoice_file, "wb") as f:
                        writer.write(f)
                    output_files.append(invoice_file)
                    invoice_count += 1
                    writer = None

                # Start a new invoice
                writer = PdfWriter()
                start_page = page_num

            writer.add_page(reader.pages[page_num])

        # Check keywords for digital PDFs
        if not ocr_flag and page_text:
            if keyword_in_top_quarter(pdf_file, page_num, keywords):
                print("Keyword found in top quarter (Digital PDF)")
                if writer is not None:
                    # Save the current invoice before starting a new one
                    invoice_file = f"{original_pdf_name}_start-page-{start_page + 1}.pdf"
                    with open(invoice_file, "wb") as f:
                        writer.write(f)
                    output_files.append(invoice_file)
                    invoice_count += 1
                    writer = None

                # Start a new invoice
                writer = PdfWriter()
                start_page = page_num

            writer.add_page(reader.pages[page_num])

    #if no keyword found, if there is previous invoice detected, group and save. Else, split and save at this point?



    # Save the last invoice
    if writer is not None:
        invoice_file = f"{original_pdf_name}_start-page-{start_page + 1}.pdf"
        with open(invoice_file, "wb") as f:
            writer.write(f)
        output_files.append(invoice_file)

    return output_files

# Function to create a ZIP file of output PDFs
def create_zip_file(files):
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        for file_name in files:
            with open(file_name, "rb") as file_content:
                zf.writestr(file_name, file_content.read())
            os.remove(file_name)
    zip_buffer.seek(0)
    return zip_buffer

# Main Streamlit app function
def main():
    st.title("Invoice Splitter")

    uploaded_file = st.file_uploader("Upload Merged Invoice PDF", type=["pdf"])

    if uploaded_file is not None:
        if st.button("Process PDF"):
            with st.spinner("Processing..."):
                output_files = split_invoices_by_keywords(uploaded_file, KEYWORDS_LIST, LOGIC_TYPE)
                zip_file = create_zip_file(output_files)

                st.success(f"Successfully split the PDF into {len(output_files)} invoices!")
                st.download_button(
                    label="Download all invoices as ZIP",
                    data=zip_file,
                    file_name="split_invoices.zip",
                    mime="application/zip"
                )

if __name__ == "__main__":
    main()
