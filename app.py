from flask import Flask, render_template, request, jsonify, send_file
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
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

class PDFProcessor:
    def __init__(self):
        # Load Groq API key from config
        self.config = configparser.ConfigParser()
        config_file = 'config.ini'
        
        if not os.path.exists(config_file):
            self.create_default_config(config_file)
        
        self.config.read(config_file)
        self.api_key = self.config.get('Groq', 'api_key', fallback='').strip()
        
        # Initialize Groq client
        self.client = None
        if self.api_key:
            try:
                self.client = Groq(api_key=self.api_key)
                # Verify the client works
                test = self.client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=1
                )
            except Exception as e:
                print(f"Error initializing Groq client: {e}")
                self.client = None
        else:
            print("No Groq API key found in config.ini")
        
        # Initialize EasyOCR
        try:
            self.reader = easyocr.Reader(['en'])
        except Exception as e:
            print(f"Failed to load EasyOCR: {e}")
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
            print(f"Failed to initialize Groq client: {e}")
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
            print(f"Error processing PDF: {e}")
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
                print(f"PDF text extraction failed, falling back to image processing: {e}")
                is_image_based = True

            if is_image_based:
                return self._process_image_based_pdf(pdf_path)
            else:
                return self._process_text_based_pdf(pdf_content)

        except Exception as e:
            print(f"PDF processing error: {e}")
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
            print(f"Image processing error: {e}")
            return None

    def _process_text_based_pdf(self, pdf_content):
        """
        Process text-based PDF content using Groq.
        """
        try:
            return self._process_text_with_groq(pdf_content)
        except Exception as e:
            print(f"Text processing error: {e}")
            return None

    def _process_text_with_groq(self, text_content):
        """
        Process text content using Groq API.
        """
        if not self.client:
            raise ValueError("Groq client not initialized. Please check your API key in config.ini")

        try:
            response = self.client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[
                    {
                        "role": "system",
                        "content": """You are an expert at analyzing various types of business and legal documents and extracting structured data.
Your task is to analyze the content and return ONLY a valid JSON object representing the relevant extracted fields based on the document type.
DO NOT include any explanations, markdown, or extra text.
Return ONLY valid JSON"""
                    },
                    {
                        "role": "user",
                        "content": f"""example JSON formats:
receipt:
{{{{
  "document_type": "receipt",
  "key_fields": {{{{
    "receipt_number": "RCPT-90432",
    "date": "2025-05-20",
    "store_name": "SuperMart",
    "store_address": "123 Elm Street, Springfield",
    "payment_method": "Credit Card",
    "total_paid": 45.99,
    "currency": "USD"
  }}}},
  "items": [
    {{{{
      "description": "Bread",
      "quantity": 1,
      "unit_price": 1.99,
      "line_total": 1.99
    }}}},
    {{{{
      "description": "Snack Pack",
      "quantity": 3,
      "unit_price": 3.0,
      "line_total": 9.0
    }}}}
  ],
  "notes": "Thank you for shopping with us!"
}}}}

bill:
{{{{
  "restaurant": {{{{
    "name": "Hotel Amer Palace, Ratanpur",
    "address": "Hosangabad Road, Ratanpur, Bhopal - 462046",
    "GSTIN": "23AADFH6301L1EN",
    "state": "Madhya Pradesh",
    "TIN": "23894007031"
  }}}},
  "bill": {{{{
    "number": "GRT1715",
    "date": "24/07/21",
    "time": "15:14",
    "steward": "MAKHAN",
    "table_cover": "34",
    "cover": 3,
    "SAC": "996332"
  }}}},
  "items": [
    {{{{
      "description": "BUTTER TANDOOR",
      "quantity": 1,
      "amount": 45.00
    }}}},
    {{{{
      "description": "MISSI ROTI",
      "quantity": 1,
      "amount": 45.00
    }}}},
    {{{{
      "description": "GARLIC NAAN",
      "quantity": 1,
      "amount": 45.00
    }}}}
  ],
  "total": {{{{
    "amount": 1315.00,
    "CGST": {{{{
      "rate": "2.5%",
      "amount": 32.88
    }}}},
    "SGST": {{{{
      "rate": "2.5%",
      "amount": 32.88
    }}}},
    "bill_amount": 1381.00
  }}}}
}}}}

invoice:
{{{{
  "document_type": "invoice",
  "key_fields": {{{{
    "document_number": "INV-20394",
    "date": "2024-05-01",
    "vendor_name": "ACME Corp",
    "customer_name": "John Doe",
    "subtotal": 500.0,
    "tax_amount": 50.0,
    "total_amount": 550.0,
    "currency": "USD",
    "due_date": "2024-05-30"
  }}}},
  "items": [
    {{{{
      "description": "Laptop",
      "quantity": 1,
      "unit_price": 500.0,
      "line_total": 500.0
    }}}}
  ],
  "notes": "Thank you for your business."
}}}}

certificate:
{{{{
  "certificate": {{{{
    "type": "Certificate of Appointment of Notary Public",
    "certificate_number": "W-00036433",
    "jurisdiction": {{{{
      "state": "Georgia",
      "county": "Fulton"
    }}}},
    "clerk": {{{{
      "name": "Cathelene Robinson",
      "title": "Clerk of Superior Court"
    }}}},
    "notary": {{{{
      "name": "Jamelia Reynolds",
      "address": "1408 Summit Springs Drive, Atlanta, GA 30350",
      "age": 23,
      "sex": "Female",
      "appointed_under": "O.C.G.A. Title 45, Ch. 17, Art. 1 as Amended",
      "term_start": "2008-09-04",
      "term_end": "2012-09-03"
    }}}},
    "witness": {{{{
      "date": "2008-09-04",
      "deputy_clerk_signature": "(Deputy Clerk's Signature)",
      "deputy_clerk_title": "Deputy Clerk of Superior Court",
      "county": "Fulton",
      "state": "Georgia"
    }}}},
    "notary_signature": "(Notary's Signature)"
  }}}}
}}}}

Analyze the following document content and return a JSON object that contains:

1. "document_type" (e.g., invoice, receipt, payment_bill, approval_letter, contract, etc.)
2. "key_fields": A dictionary of relevant fields and their values, depending on the type of document.
3. "items": If the document includes a list of items (like an invoice or bill), include them as an array of objects with fields like description, quantity, unit_price, line_total, etc.
4. "notes": Any additional textual information or footnotes present.

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
            print(f"Groq API error: {e}")
            return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_pdf():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not file.filename.endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are allowed'}), 400

    try:
        # Save uploaded file
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Process PDF
        processor = PDFProcessor()
        data = processor._process_pdf_with_groq(filepath)

        # Clean up uploaded file
        os.remove(filepath)

        if data:
            return jsonify(data)
        else:
            return jsonify({'error': 'Failed to process PDF'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/export/<format>', methods=['POST'])
def export_data(format):
    try:
        data = request.json
        
        if format == 'excel':
            # Create Excel file
            output = f"extracted_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            
            with pd.ExcelWriter(output) as writer:
                # Document Info sheet
                doc_info = {k: v for k, v in data.items() if k not in ['items', 'metadata']}
                pd.DataFrame([doc_info]).to_excel(writer, sheet_name='Document Info', index=False)
                
                # Line Items sheet
                if 'items' in data and data['items']:
                    pd.DataFrame(data['items']).to_excel(writer, sheet_name='Line Items', index=False)
                
                # Metadata sheet
                if 'metadata' in data:
                    pd.DataFrame([data['metadata']]).to_excel(writer, sheet_name='Metadata', index=False)
            
            return send_file(output, as_attachment=True, download_name=output)
            
        elif format == 'json':
            # Create JSON file
            output = f"extracted_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(output, 'w') as f:
                json.dump(data, f, indent=2)
            
            return send_file(output, as_attachment=True, download_name=output)
        
        else:
            return jsonify({'error': 'Invalid export format'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True) 