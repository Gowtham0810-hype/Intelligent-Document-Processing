import streamlit as st
import os
import json
import pandas as pd
from datetime import datetime
import fitz  # PyMuPDF
import easyocr
import configparser
from groq import Groq
import re
import base64
from PIL import Image

class PDFProcessor:
    def __init__(self):
        # Load Groq API key from config
        self.config = configparser.ConfigParser()
        config_file = 'config.ini'
        
        if not os.path.exists(config_file):
            self.create_default_config(config_file)
        
        self.config.read(config_file)
        self.api_key = self.config.get('Groq', 'api_key', fallback='')
        
        # Initialize Groq client
        if self.api_key:
            try:
                self.client = Groq(api_key=self.api_key)
            except Exception as e:
                st.error(f"Error initializing Groq client: {e}")
                self.client = None
        
        # Initialize EasyOCR
        try:
            self.reader = easyocr.Reader(['en'])
        except Exception as e:
            st.error(f"Failed to load EasyOCR: {e}")
            self.reader = None

    def create_default_config(self, config_file):
        self.config['Groq'] = {'api_key': ''}
        with open(config_file, 'w') as f:
            self.config.write(f)

    def save_api_key(self, api_key):
        self.api_key = api_key
        self.config['Groq']['api_key'] = api_key
        with open('config.ini', 'w') as f:
            self.config.write(f)
        try:
            self.client = Groq(api_key=self.api_key)
            return True
        except Exception as e:
            st.error(f"Failed to initialize Groq client: {e}")
            return False

    def process_pdf(self, pdf_file):
        try:
            # Create a temporary file to save the uploaded file
            temp_pdf_path = f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            with open(temp_pdf_path, "wb") as f:
                f.write(pdf_file.getvalue())

            # Process the PDF
            data = self._process_pdf_with_groq(temp_pdf_path)
            
            # Clean up temporary file
            os.remove(temp_pdf_path)
            
            return data
        except Exception as e:
            st.error(f"Error processing PDF: {e}")
            return None

    def _process_pdf_with_groq(self, pdf_path):
        """
        Process PDF using Groq, handling both traditional PDFs and image-based PDFs.
        """
        try:
            # First try traditional PDF text extraction
            pdf_content = ""
            is_image_based = False
            
            try:
                with fitz.open(pdf_path) as doc:
                    for page_num in range(len(doc)):
                        page = doc[page_num]
                        text = page.get_text()
                        if text.strip():
                            pdf_content += text + "\n\n"
                    
                    if not pdf_content.strip():
                        is_image_based = True
            except Exception as e:
                st.warning(f"PDF text extraction failed, falling back to image processing: {e}")
                is_image_based = True

            if is_image_based:
                return self._process_image_based_pdf(pdf_path)
            else:
                return self._process_text_based_pdf(pdf_content)

        except Exception as e:
            st.error(f"PDF processing error: {e}")
            return None

    def _process_image_based_pdf(self, pdf_path):
        """
        Process image-based PDF using OCR and Groq.
        """
        try:
            # Convert PDF pages to images
            image_paths = []
            with fitz.open(pdf_path) as doc:
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
                    temp_img_path = f"temp_page_{page_num}.png"
                    pix.save(temp_img_path)
                    image_paths.append(temp_img_path)

            combined_data = {
                "document_type": None,
                "items": [],
                "metadata": {
                    "page_count": len(image_paths),
                    "processing_date": datetime.now().strftime("%Y-%m-%d"),
                    "confidence_score": 0.0
                }
            }

            for idx, img_path in enumerate(image_paths):
                # Extract text using EasyOCR
                ocr_text = "\n".join(self.reader.readtext(img_path, detail=0))
                
                # Process with Groq
                page_data = self._process_text_with_groq(ocr_text)
                
                if page_data:
                    if idx == 0:
                        combined_data.update({k: v for k, v in page_data.items() if k not in ['items', 'metadata']})
                    if 'items' in page_data:
                        combined_data['items'].extend(page_data['items'])

                # Clean up temporary image
                try:
                    os.remove(img_path)
                except:
                    pass

            return combined_data

        except Exception as e:
            st.error(f"Image processing error: {e}")
            return None

    def _process_text_based_pdf(self, pdf_content):
        """
        Process text-based PDF content using Groq.
        """
        try:
            return self._process_text_with_groq(pdf_content)
        except Exception as e:
            st.error(f"Text processing error: {e}")
            return None

    def _process_text_with_groq(self, text_content):
        """
        Process text content using Groq API.
        """
        try:
            response = self.client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[
                    {
                        "role": "system",
                        "content": """You are an expert at analyzing documents and extracting structured data.
                        Your task is to analyze the content and extract ONLY the requested JSON data structure.
                        DO NOT include any additional text, explanations, or markdown formatting in your response.
                        ONLY return valid JSON."""
                    },
                    {
                        "role": "user",
                        "content": f"""Extract the following information in strict JSON format:

{{
    "document_type": "invoice/bill/receipt",
    "document_number": "TEXT_OR_NUMBER",
    "date": "YYYY-MM-DD",
    "vendor_name": "TEXT",
    "vendor_address": "TEXT",
    "vendor_tax_id": "TEXT",
    "customer_name": "TEXT",
    "customer_address": "TEXT",
    "customer_tax_id": "TEXT",
    "currency": "TEXT_CODE_USD_EUR_INR",
    "subtotal": 0.0,
    "tax_amount": 0.0,
    "total_amount": 0.0,
    "due_date": "YYYY-MM-DD",
    "payment_terms": "TEXT",
    "items": [
        {{
            "description": "TEXT",
            "quantity": 0.0,
            "unit_price": 0.0,
            "line_total": 0.0
        }}
    ],
    "notes": "TEXT"
}}

Document content:
{text_content}"""
                    }
                ],
                temperature=0.1,
                max_tokens=4000
            )

            result = response.choices[0].message.content.strip()
            
            # Try multiple approaches to extract and parse JSON
            data = None
            
            # Attempt 1: Direct JSON parsing
            try:
                data = json.loads(result)
            except json.JSONDecodeError:
                # Attempt 2: Find JSON-like structure with regex
                json_matches = re.finditer(r'({(?:[^{}]|{[^{}]*})*})', result)
                for match in json_matches:
                    try:
                        potential_json = match.group(1)
                        data = json.loads(potential_json)
                        if isinstance(data, dict) and "document_type" in data:
                            break
                    except:
                        continue
                
                # Attempt 3: Try to fix common JSON formatting issues
                if not data:
                    try:
                        cleaned_result = result.replace("'", '"')
                        cleaned_result = re.sub(r'(\w+):', r'"\1":', cleaned_result)
                        cleaned_result = re.sub(r',\s*}', '}', cleaned_result)
                        data = json.loads(cleaned_result)
                    except:
                        pass
                
                if not data:
                    raise ValueError("Could not parse valid JSON from response")

            return data

        except Exception as e:
            st.error(f"Groq API error: {e}")
            return None

def main():
    st.set_page_config(
        page_title="WVF Intelligent Data Processor",
        page_icon="📄",
        layout="wide"
    )

    # Initialize processor with default configuration
    processor = PDFProcessor()

    # Main content
    st.title("WVF Intelligent Data Processor")
    st.markdown("---")

    # File upload
    uploaded_file = st.file_uploader("Drop or select a PDF file", type=["pdf"])

    if uploaded_file:
        # Process button
        if st.button("Process Document"):
            with st.spinner("Processing PDF..."):
                # Process the PDF
                data = processor.process_pdf(uploaded_file)
                
                if data:
                    # Store the processed data in session state
                    st.session_state.processed_data = data
                    st.session_state.doc_info = {k: v for k, v in data.items() if k not in ['items', 'metadata']}
                    st.session_state.df_doc = pd.DataFrame([st.session_state.doc_info])
                    if 'items' in data and data['items']:
                        st.session_state.df_items = pd.DataFrame(data['items'])

    # Display results if data has been processed
    if 'processed_data' in st.session_state:
        data = st.session_state.processed_data
        
        # Create tabs for different views
        doc_tab, items_tab, json_tab = st.tabs(["📄 Document Info", "📋 Line Items", "🔍 Raw JSON"])
        
        with doc_tab:
            st.subheader("Document Information")
            # Make document info editable
            edited_doc_df = st.data_editor(
                st.session_state.df_doc,
                use_container_width=True,
                num_rows="fixed",
                key="doc_editor"
            )
            # Update session state with edited data
            if st.session_state.df_doc.equals(edited_doc_df) is False:
                st.session_state.df_doc = edited_doc_df
                # Update processed_data with edited document info
                for col in edited_doc_df.columns:
                    st.session_state.processed_data[col] = edited_doc_df.iloc[0][col]

        with items_tab:
            st.subheader("Line Items")
            if 'df_items' in st.session_state:
                # Make items table editable
                edited_items_df = st.data_editor(
                    st.session_state.df_items,
                    use_container_width=True,
                    num_rows="dynamic",  # Allow adding/removing rows
                    key="items_editor"
                )
                # Update session state with edited data
                if not st.session_state.df_items.equals(edited_items_df):
                    st.session_state.df_items = edited_items_df
                    # Update processed_data with edited items
                    st.session_state.processed_data['items'] = edited_items_df.to_dict('records')
                
                # Calculate and display total
                if 'line_total' in edited_items_df.columns:
                    total = edited_items_df['line_total'].sum()
                    st.metric("Total Amount", f"${total:,.2f}")

        with json_tab:
            st.subheader("Raw JSON Data")
            st.json(st.session_state.processed_data)  # Show updated JSON

        # Export buttons section below tabs
        st.markdown("---")
        st.subheader("Export Options")
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Export to Excel button
            if st.button("Export to Excel"):
                output = f"extracted_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                with pd.ExcelWriter(output) as writer:
                    # Document Info sheet with edited data
                    st.session_state.df_doc.to_excel(writer, sheet_name='Document Info', index=False)
                    
                    # Line Items sheet with edited data
                    if 'df_items' in st.session_state:
                        st.session_state.df_items.to_excel(writer, sheet_name='Line Items', index=False)
                    
                    # Metadata sheet
                    if 'metadata' in st.session_state.processed_data:
                        df_meta = pd.DataFrame([st.session_state.processed_data['metadata']])
                        df_meta.to_excel(writer, sheet_name='Metadata', index=False)
                
                # Read the Excel file and create a download button
                with open(output, 'rb') as f:
                    excel_data = f.read()
                
                st.download_button(
                    label="Download Excel File",
                    data=excel_data,
                    file_name=output,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
                # Clean up the temporary file
                try:
                    os.remove(output)
                except:
                    pass

        with col2:
            # Export to JSON button
            if st.button("Export to JSON"):
                json_str = json.dumps(st.session_state.processed_data, indent=2)
                st.download_button(
                    label="Download JSON File",
                    data=json_str,
                    file_name=f"extracted_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json"
                )

if __name__ == "__main__":
    main() 