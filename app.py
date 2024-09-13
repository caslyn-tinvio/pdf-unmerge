import re
import os
from PyPDF2 import PdfReader, PdfWriter
import streamlit as st
import zipfile
from io import BytesIO

KEYWORDS_LIST = ["Invoice"]
LOGIC_TYPE = "OR"

# Function to extract text from each page of the PDF
def extract_text_from_page(reader, page_num):
    try:
        page = reader.pages[page_num]
        return page.extract_text()
    except Exception as e:
        st.warning(f"Warning: Could not extract text from page {page_num + 1}: {e}")
        return None

# Updated function to detect continuation pages based on content
def is_continuation_page(text):
    # Look for specific continuation indicators
    continuation_keywords = ["Continued"]  # Other words like 'Page' are too generic

    # Check for keywords like 'Continued'
    if any(keyword in text for keyword in continuation_keywords):
        return True

    # Check for patterns like 'Page X of Y', but only treat it as continuation if X > 1
    page_pattern = r"Page\s+(\d+)\s+of\s+(\d+)"
    match = re.search(page_pattern, text)
    if match:
        current_page = int(match.group(1))
        total_pages = int(match.group(2))

        # It's only a continuation if it's not the first page
        if current_page > 1 and total_pages > 1:
            return True

    return False

# Function to check if a page contains the keywords based on the specified logic
def keywords_in_text(text, keywords, logic_type="OR"):
    if text is None:
        return False
    if logic_type == "OR":
        # Return True if any keyword is found
        return any(keyword in text for keyword in keywords)
    elif logic_type == "AND":
        # Return True only if all keywords are found
        return all(keyword in text for keyword in keywords)
    return False

# Function to split the merged PDF into individual invoice PDFs
def split_invoices_by_keywords(pdf_file, keywords, logic_type="OR"):
    original_pdf_name = os.path.splitext(os.path.basename(pdf_file.name))[0]
    reader = PdfReader(pdf_file)

    invoice_count = 0
    writer = None
    output_files = []
    start_page = 0
    last_keyword_page = None

    # Loop through pages in the PDF
    for page_num in range(len(reader.pages)):
        page_text = extract_text_from_page(reader, page_num)

        # If page text cannot be extracted, treat the page as a separate invoice
        if page_text is None or page_text.strip() == "":
            # Save the previous invoice (if any)
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
            writer = None  # Close this single-page "invoice"
            continue

        # Skip splitting if it's a continuation page
        if is_continuation_page(page_text):
            if writer is not None:
                writer.add_page(reader.pages[page_num])
            continue

        # If keywords are found according to logic_type, treat it as the start of a new invoice
        if keywords_in_text(page_text, keywords, logic_type):
            # Save the previous invoice (if any)
            if writer is not None:
                invoice_file = f"{original_pdf_name}_start-page-{start_page + 1}.pdf"
                with open(invoice_file, "wb") as f:
                    writer.write(f)
                output_files.append(invoice_file)
                invoice_count += 1
                writer = None

            # Start a new invoice
            writer = PdfWriter()
            start_page = page_num  # Track the starting page for this new invoice
            last_keyword_page = page_num  # Track last page where a keyword was found

        # Continue adding pages to the current invoice
        if writer is not None:
            writer.add_page(reader.pages[page_num])

    # Save the last invoice (if any)
    if writer is not None:
        invoice_file = f"{original_pdf_name}_start-page-{start_page + 1}.pdf"
        with open(invoice_file, "wb") as f:
            writer.write(f)
        output_files.append(invoice_file)

    return output_files

# Function to create a ZIP file of the output PDFs
def create_zip_file(files):
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        for file_name in files:
            with open(file_name, "rb") as file_content:
                zf.writestr(file_name, file_content.read())
    zip_buffer.seek(0)
    return zip_buffer

# Main Streamlit app function
def main():
    st.title("Invoice Splitter")

    # File upload widget
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
