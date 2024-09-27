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
    "Invoice Date", "Date of Issue", "Issue Date", "Billing Date", "Transaction Date", "Bill Date"

    # Payment Terms and Conditions
    "Terms", "Term", "Payment Term", "Payment Terms", "Delivery Term", "Delivery Terms", "Net Terms", "Terms and Conditions",

    # Customer and Supplier Information
    "Bill To", "Billed To", "Invoice To", "Invoiced To", "Sold To", "Customer", "Client", "Buyer",
    "Ship To", "Shipping Address", "Delivery Address", "Deliver To", "Consignee", "Billing Address", "Bill From"

    # Financial and Tax Information
    "Tax ID", "VAT Number", "GST Number", "Tax Registration", "Company Registration",
    "Taxable Amount", "Tax Invoice", "GST", "GST Reg No", "UEN"

    # Contact and Company Information
    "Contact", "Contact Details", "Phone", "Email", "Fax", "Website", "Customer Service", "Support",


    # Other Relevant Keywords
    "Account Summary", "Statement", "Billing Statement", "Reference", "Reference No", "Reference Number",
    "Transaction ID", "Customer ID", "Client ID", "Account Number", "Payment Method",

    #Other payment related keywords
    "Payment Voucher"
]

LOGIC_TYPE = "OR"

# Function to extract text using OCR for scanned PDFs
def ocr_extract_text_from_page(reader, page_num):
    # Extract the specific page as a new single-page PDF
    try:
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
        return None #OCRed, but nothing was read
    except Exception as e:
        print("OCR failed") #OCR failed, return nothing
        return None


# Function to check if keyword appears in the top 1/4 of the image for scanned PDFs
def ocr_keyword_in_top_third(image, keywords):
    ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    image_height = image.size[1]  # Height of the image

    lines = {}
    num_words = len(ocr_data["text"])

    # Build a dictionary of lines with their corresponding words and positions
    for i in range(num_words):
        word = ocr_data["text"][i]
        if not word.strip():
            continue

        line_num = ocr_data["line_num"][i]
        word_top = int(ocr_data["top"][i])

        if line_num not in lines:
            lines[line_num] = {
                "words": [],
                "top_positions": [],
            }

        lines[line_num]["words"].append(word)
        lines[line_num]["top_positions"].append(word_top)

    # Accumulate lines that are in the top third of the image
    top_third_lines = []
    for line_num, line_data in lines.items():
        min_top = min(line_data["top_positions"])
        if min_top < (image_height / 3):  # Check if the line is in the top 1/3
            line_text = " ".join(line_data["words"])
            top_third_lines.append(line_text)

    # Combine all the top third lines into a single string
    top_third_text = " ".join(top_third_lines).lower()
    print("top_third_text:",top_third_text)

    # Check for multi-line keywords
    for keyword in keywords:
        if keyword.lower() in top_third_text:
            return True  # Keyword found in the top third of the image

    return False


# Function to extract text from each page of the PDF (digital PDF)
def extract_text_from_page(reader, page_num):
    try:
        page = reader.pages[page_num]
        return page.extract_text().lower()  # Convert to lowercase for checking
    except Exception as e:
        st.warning(f"Warning: Could not extract text from page {page_num + 1}: {e}")
        return None


# Function to check if a keyword appears in the top 1/3 of the page for digital PDFs
def keyword_in_top_third(pdf_file, page_num, keywords):
    for page_layout in extract_pages(pdf_file, page_numbers=[page_num]):
        page_height = page_layout.height

        for element in page_layout:
            if isinstance(element, LTTextContainer):
                for text_line in element:
                    text = text_line.get_text()
                    if any(keyword.lower() in text.lower() for keyword in keywords):
                        # Get the vertical position of the text line
                        y = text_line.bbox[1]  # Get the y-coordinate (bottom) of the text line
                        if y > (page_height * 2 / 3):  # Top 1/3 of the page
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
    #initialize reader for processing PDF
    reader = PdfReader(pdf_file)
    original_pdf_name = os.path.splitext(os.path.basename(pdf_file.name))[0]

    #initialize variables
    invoice_count = 0
    writer = None
    output_files = []
    last_transaction_page = 0 #tracks starting page of last detected transaction page, starts/sets to first page by default

    for current_page in range(len(reader.pages)):
        page_text = extract_text_from_page(reader, current_page)
        ocr_flag = False  # Flag to check if OCR was used

        # Try OCR if text extraction from digital PDF fails
        if page_text is None or page_text.strip() == "":
            page_text = ocr_extract_text_from_page(reader, current_page)
            ocr_flag = True

        print(f"Processing page {current_page + 1}")
        print(f"Extracted text: {page_text if page_text else 'No text extracted'}") #get first 100 char
        print(f"OCR used: {ocr_flag}")

        # If both text extraction and OCR fail, or OCR returns nothing e.g. blank page, mark the page as unreadable and split
        if page_text is None:
            print("Text extraction and OCR failed")
            if writer is not None: #save previous file, if any
                invoice_file = f"{original_pdf_name}_start-page-{last_transaction_page + 1}.pdf"
                with open(invoice_file, "wb") as f:
                    writer.write(f)
                output_files.append(invoice_file)
                invoice_count += 1
                writer = None

            # Create a new writer for the unreadable page
            writer = PdfWriter()
            writer.add_page(reader.pages[current_page])
            invoice_file = f"{original_pdf_name}_start-page-{current_page + 1}_unreadable.pdf"
            with open(invoice_file, "wb") as f:
                writer.write(f)
            output_files.append(invoice_file)
            invoice_count += 1
            writer = None
            continue

        # Detect continuation pages
        if is_continuation_page(page_text):
            if writer is None:
                writer = PdfWriter()
                last_transaction_page = current_page
            writer.add_page(reader.pages[current_page])  # Add to the current writer instance
            continue

        keyword_found = False  # Flag to indicate if keyword was found

        # Check keywords for scanned PDFs (OCR-based)
        if ocr_flag and page_text:
            # Convert the page to an image and check for keywords in the top third
            pdf_writer = PdfWriter()
            pdf_writer.add_page(reader.pages[current_page])

            # Create a byte stream for the single-page PDF
            pdf_bytes_io = BytesIO()
            pdf_writer.write(pdf_bytes_io)
            pdf_bytes_io.seek(0)

            # Convert the page to an image for OCR processing
            image = convert_from_bytes(pdf_bytes_io.read(), first_page=1, last_page=1)[0]
            if ocr_keyword_in_top_third(image, keywords):
                keyword_found = True

        # Check keywords for digital PDFs
        if not ocr_flag and page_text:
            if keyword_in_top_third(pdf_file, current_page, keywords):
                keyword_found = True

        if keyword_found:
            print("Keyword found in top third")
            if writer is not None: #there is a current running invoice
                # Save the current invoice before starting a new one
                invoice_file = f"{original_pdf_name}_start-page-{last_transaction_page + 1}.pdf"
                with open(invoice_file, "wb") as f:
                    writer.write(f)
                output_files.append(invoice_file)
                invoice_count += 1
                writer = None #clean up writer step

            # Start a new invoice
            writer = PdfWriter()
            last_transaction_page = current_page
            writer.add_page(reader.pages[current_page])  # Add current page to new writer
            continue

        # If no keyword found, add page to current invoice
        if writer is None:
            writer = PdfWriter()
            last_transaction_page = current_page
        print("No keyword found, grouping page with last detected invoice")
        writer.add_page(reader.pages[current_page])


    # Save the last invoice
    if writer is not None and len(writer.pages) > 0:
        invoice_file = f"{original_pdf_name}_start-page-{last_transaction_page + 1}.pdf"
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
    st.title("Invoice/Bill Unmerge tool")
    st.markdown("Upload a singular PDF containing multiple merged invoice/bills. Our tool will automatically unmerge them into individual invoice/bills for you!")

    uploaded_file = st.file_uploader("Upload merged invoice/bills pdf", type=["pdf"])

    if uploaded_file is not None:
        if st.button("Process PDF"):
            with st.spinner("Processing..."):
                try:
                    print("Processing new merged pdf")
                    output_files = split_invoices_by_keywords(uploaded_file, KEYWORDS_LIST, LOGIC_TYPE)
                    zip_file = create_zip_file(output_files)
                    with open("local_vers.zip", "wb") as f:
                        f.write(zip_file.read())

                    st.success(f"Successfully split the PDF into {len(output_files)} invoices!")
                    st.download_button(
                        label="Download all invoices as ZIP",
                        data=zip_file,
                        file_name="split_invoices.zip",
                        mime="application/zip"
                    )

                except Exception as e:
                    print(f"Error: {e}")
                    # Display a generic error message to the user
                    st.error("An error has occurred. Please try again later.")



if __name__ == "__main__":
    main()
