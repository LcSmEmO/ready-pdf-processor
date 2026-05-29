from fastapi import FastAPI, UploadFile, File
from pypdf import PdfReader
import tempfile
import os
import re

app = FastAPI()

def clean_extracted_text(text: str) -> str:
    ligature_replacements = {
        "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
    }
    for ligature, replacement in ligature_replacements.items():
        text = text.replace(ligature, replacement)
    
    # Corrige quebras de hífens comuns em final de linha
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)
    # Normaliza espaços em branco
    text = re.sub(r"\s+", " ", text).strip()
    return text

@app.post("/process-pdf")
async def process_pdf(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
        temp.write(await file.read())
        temp_path = temp.name

    try:
        reader = PdfReader(temp_path)
        result = []
        block_index = 1

        # Percorre as páginas do PDF usando um motor leve
        for page_idx, page in enumerate(reader.pages):
            page_number = page_idx + 1
            raw_text = page.extract_text()
            
            if not raw_text:
                continue

            # Divide o texto por parágrafos/quebras de linha duplas
            paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]

            for para in paragraphs:
                processed_text = clean_extracted_text(para)
                
                if not processed_text or len(processed_text) <= 2:
                    continue

                # Define o tipo de bloco baseado em características simples
                block_type = "paragraph"
                if len(processed_text) < 60 and not processed_text.endswith((".", "!", "?")):
                    block_type = "title"
                elif re.match(r"^\d{1,2}\.\s+", processed_text) or "(N. do T.)" in processed_text:
                    block_type = "footnote"

                result.append({
                    "order": block_index,
                    "type": block_type,
                    "originalText": processed_text,
                    "page": page_number,
                    "sourceType": "NarrativeText" if block_type == "paragraph" else "Title"
                })
                block_index += 1

        return {
            "filename": file.filename,
            "blocks": result
        }

    finally:
        os.remove(temp_path)