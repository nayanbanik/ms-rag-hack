import asyncio
import openai
import html
import logging
from typing import IO, AsyncGenerator, Union
import os
from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import DocumentTable
from azure.core.credentials import AzureKeyCredential
from azure.core.credentials_async import AsyncTokenCredential
from pypdf import PdfReader
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
import base64
from .page import Page
from .parser import Parser
from openai import AsyncAzureOpenAI, AsyncOpenAI
logger = logging.getLogger("ingester")



async def get_summary(client, data, type='image'):
    deployment_name = 'gpt4o'
    try:
        if type == 'image':
            image_summary_prompt = """
                This image includes visual elements that are integral to understanding the content on the page.
                Please describe the provided image capturing the main message, trends, or relationships depicted.
            """
            response = await client.chat.completions.create(
                model=deployment_name,
                messages=[
                    { "role": "system", "content": "You are a helpful assistant." },
                    { "role": "user", "content": [  
                        { 
                            "type": "text", 
                            "text": f"{image_summary_prompt}" 
                        },
                        { 
                            "type": "image_url",
                            "image_url": {
                                "url": f"{data}",
                            }
                        }
                    ] } 
                ],
            )
            summary = "Image: " + response.choices[0].message.content
            return summary
        else:
            page_summary_prompt = """
            I have a collection of textual representations from various components of a page. These components include the raw text and summarized descriptions of tables and images. Each of these elements provides key information and context relevant to the content on this page.
            Please generate a comprehensive and coherent summary paragraph in plain text without markdown that encapsulates the main ideas, important details, and key insights from these components. Ensure that the summary is well-structured and reflects the relationships between the different types of content on this page.
            """
            response = await client.chat.completions.create(
                model=deployment_name,
                messages=[
                    { "role": "system", "content": "You are a helpful assistant." },
                    { "role": "user", "content": [  
                        { 
                            "type": "text", 
                            "text": f"{page_summary_prompt}" 
                        },
                        { 
                            "type": "text",
                            "text": f"{data}"
                        }
                    ] } 
                ],
            )
            summary = response.choices[0].message.content
            return summary
    except openai.BadRequestError as e:
        if 'ResponsibleAIPolicyViolation' in str(e):
            logger.error(f"Content policy violation: {e}")
            return "[Content removed due to policy violation]"
        else:
            logger.error(f"Bad request error: {e}")
            return "[An error occurred while processing the content]"
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return "[An unexpected error occurred]"

class LocalPdfParser(Parser):

    async def parse(self, content: IO) -> AsyncGenerator[Page, None]:
        max_retries = 5  # Define maximum number of retries
        attempts = 0

        logger.info("Extracting text from '%s' using local PDF parser (pypdf)", content.name)

        # azure_credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
        AZURE_OPENAI_KEY = os.getenv("AZURE_IMAGE_INFERENCING_OPENAI_KEY")
        api_version = os.getenv("AZURE_IMAGE_INFERENCING_API_VERSION")
        endpoint = os.getenv("AZURE_IMAGE_INFERENCING_ENDPOINT")

        while attempts < max_retries:
            try:
                async with AsyncAzureOpenAI(api_version=api_version, azure_endpoint=endpoint, api_key=AZURE_OPENAI_KEY) as client:
                    reader = PdfReader(content)
                    pages = reader.pages
                    offset = 0
                    for page_num, p in enumerate(pages):
                        page_text = p.extract_text()
                        for count, image_file_object in enumerate(p.images):
                            file_name = f"{page_num + 1}-{image_file_object.name}"
                            if '.jp2' in file_name:
                                continue
                            with open(file_name, "w+b") as fp:
                                fp.write(image_file_object.data)  # Write the image data directly to the file
                                fp.flush()  # Ensure all data is written to the file
                                fp.seek(0)
                                image_data = fp.read()
                                # Convert the image data to a base64 string
                                image_base64 = base64.b64encode(image_data).decode('utf-8')
                                image_data_url = f"data:image/png;base64,{image_base64}"  # Assuming PNG format, adjust if necessary
                                page_text += await get_summary(client, image_data_url, 'image')

                            # After processing, delete the file
                            if os.path.exists(file_name):
                                os.remove(file_name)
                                logger.info(f"File {file_name} deleted successfully.")
                            else:
                                logger.warning(f"File {file_name} does not exist.")
                        # full_page_text = await get_summary(client, page_text, type='page') # Print the combined text for the page
                        print(page_text)  # Print the combined text for the page
                        yield Page(page_num=page_num, offset=offset, text=page_text)
                        offset += len(page_text)
                    break
                    # finally:
                    # await client.close()
                    # if hasattr(client, 'azure_ad_token_provider'):
                    #     await client.azure_ad_token_provider.close()
            except openai.error.AuthenticationError as e:
                attempts += 1  # Increment attempts on authentication errors
                logger.error(f"Authentication error occurred: {e}. Attempting to retry ({attempts}/{max_retries})...")

                if attempts >= max_retries:
                    logger.error("Maximum retry attempts reached. Aborting operation.")
                    yield Page(page_num=-1, offset=0, text="[Authentication failed after multiple attempts]")
                    return
            except openai.error.OpenAIError as e:
                logger.error(f"OpenAI error occurred: {e}")
                yield Page(page_num=-1, offset=0, text="[An error occurred during parsing]")
                return
            except Exception as e:
                logger.error(f"An unexpected error occurred: {e}")
                yield Page(page_num=-1, offset=0, text="[An unexpected error occurred]")
                return


class DocumentAnalysisParser(Parser):
    """
    Concrete parser backed by Azure AI Document Intelligence that can parse many document formats into pages
    To learn more, please visit https://learn.microsoft.com/azure/ai-services/document-intelligence/overview
    """

    def __init__(
        self, endpoint: str, credential: Union[AsyncTokenCredential, AzureKeyCredential], model_id="prebuilt-layout"
    ):
        self.model_id = model_id
        self.endpoint = endpoint
        self.credential = credential

    async def parse(self, content: IO) -> AsyncGenerator[Page, None]:
        logger.info("Extracting text from '%s' using Azure Document Intelligence", content.name)

        async with DocumentIntelligenceClient(
            endpoint=self.endpoint, credential=self.credential
        ) as document_intelligence_client:
            poller = await document_intelligence_client.begin_analyze_document(
                model_id=self.model_id, analyze_request=content, content_type="application/octet-stream"
            )
            form_recognizer_results = await poller.result()

            offset = 0
            for page_num, page in enumerate(form_recognizer_results.pages):
                tables_on_page = [
                    table
                    for table in (form_recognizer_results.tables or [])
                    if table.bounding_regions and table.bounding_regions[0].page_number == page_num + 1
                ]

                # mark all positions of the table spans in the page
                page_offset = page.spans[0].offset
                page_length = page.spans[0].length
                table_chars = [-1] * page_length
                for table_id, table in enumerate(tables_on_page):
                    for span in table.spans:
                        # replace all table spans with "table_id" in table_chars array
                        for i in range(span.length):
                            idx = span.offset - page_offset + i
                            if idx >= 0 and idx < page_length:
                                table_chars[idx] = table_id

                # build page text by replacing characters in table spans with table html
                page_text = ""
                added_tables = set()
                for idx, table_id in enumerate(table_chars):
                    if table_id == -1:
                        page_text += form_recognizer_results.content[page_offset + idx]
                    elif table_id not in added_tables:
                        page_text += DocumentAnalysisParser.table_to_html(tables_on_page[table_id])
                        added_tables.add(table_id)

                yield Page(page_num=page_num, offset=offset, text=page_text)
                offset += len(page_text)

    @classmethod
    def table_to_html(cls, table: DocumentTable):
        table_html = "<table>"
        rows = [
            sorted([cell for cell in table.cells if cell.row_index == i], key=lambda cell: cell.column_index)
            for i in range(table.row_count)
        ]
        for row_cells in rows:
            table_html += "<tr>"
            for cell in row_cells:
                tag = "th" if (cell.kind == "columnHeader" or cell.kind == "rowHeader") else "td"
                cell_spans = ""
                if cell.column_span is not None and cell.column_span > 1:
                    cell_spans += f" colSpan={cell.column_span}"
                if cell.row_span is not None and cell.row_span > 1:
                    cell_spans += f" rowSpan={cell.row_span}"
                table_html += f"<{tag}{cell_spans}>{html.escape(cell.content)}</{tag}>"
            table_html += "</tr>"
        table_html += "</table>"
        return table_html
