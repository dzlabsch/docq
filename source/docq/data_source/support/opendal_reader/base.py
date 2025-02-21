# The MIT License

# Copyright (c) Jerry Liu

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""OpenDAL file and directory reader.

A loader that fetches a file or iterates through a directory on a object store like AWS S3 or AzureBlob.

"""
import asyncio
import logging as log
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Self, Type, Union, cast

import opendal
from llama_index.readers.base import BaseReader
from llama_index.readers.file.docs_reader import DocxReader, PDFReader
from llama_index.readers.file.epub_reader import EpubReader
from llama_index.readers.file.image_reader import ImageReader
from llama_index.readers.file.ipynb_reader import IPYNBReader
from llama_index.readers.file.markdown_reader import MarkdownReader
from llama_index.readers.file.mbox_reader import MboxReader
from llama_index.readers.file.slides_reader import PptxReader
from llama_index.readers.file.tabular_reader import PandasCSVReader
from llama_index.readers.file.video_audio_reader import VideoAudioReader
from llama_index.schema import Document

from .... import services
from ....domain import DocumentListItem

DEFAULT_FILE_READER_CLS: Dict[str, Type[BaseReader]] = {
    ".pdf": PDFReader,
    ".docx": DocxReader,
    ".pptx": PptxReader,
    ".jpg": ImageReader,
    ".png": ImageReader,
    ".jpeg": ImageReader,
    ".mp3": VideoAudioReader,
    ".mp4": VideoAudioReader,
    ".csv": PandasCSVReader,
    ".epub": EpubReader,
    ".md": MarkdownReader,
    ".mbox": MboxReader,
    ".ipynb": IPYNBReader,
}

FILE_MIME_EXTENSION_MAP: Dict[str, str] = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.google-apps.document": ".gdoc",
    "application/vnd.google-apps.presentation": ".gslides",
    "application/vnd.google-apps.spreadsheet": ".gsheet",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/jpg": ".jpg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "video/mp4": ".mp4",
    "video/mpeg": ".mp4",
    "text/csv": ".csv",
    "application/epub+zip": ".epub",
    "text/markdown": ".md",
    "application/x-ipynb+json": ".ipynb",
    "application/mbox": ".mbox",
}


class OpendalReader(BaseReader):
    """General reader for any opendal operator."""

    def __init__(
        self: Self,
        scheme: str,
        path: str = "/",
        file_extractor: Optional[Dict[str, Union[str, BaseReader]]] = None,
        file_metadata: Optional[Callable[[str], Dict]] = None,
        **kwargs: Optional[dict[str, Any]],
    ) -> None:
        """Initialize opendal operator, along with credentials if needed.

        Args:
            scheme (str): the scheme of the service
            path (str): the path of the data. If none is provided,
                this loader will iterate through the entire bucket. If path is endswith `/`, this loader will iterate through the entire dir. Otherwise, this loader will load the file.
            file_extractor (Optional[Dict[str, BaseReader]]): A mapping of file
                extension to a BaseReader class that specifies how to convert that file
                to text. NOTE: this isn't implemented yet.
            file_metadata (Optional[Callable[[str], Dict]]): A function that takes a source file path and returns a dictionary of metadata to be added to the Document object.
            **kwargs (Optional dict[str, any]): Additional arguments to pass to the `opendal.AsyncOperator` constructor. These are the scheme (object store) specific options.
        """
        super().__init__()
        self.path = path
        self.file_metadata = file_metadata

        self.supported_suffix = list(DEFAULT_FILE_READER_CLS.keys())

        self.async_op = opendal.AsyncOperator(scheme, **kwargs)

        if file_extractor is not None:
            self.file_extractor = file_extractor
        else:
            self.file_extractor = {}

        self.documents: List[Document] = []

    def load_data(self: Self) -> List[Document]:
        """Load file(s) from OpenDAL."""
        # TODO: think about the private and secure aspect of this temp folder.
        # NOTE: the following code cleans up the temp folder when existing the context.

        with tempfile.TemporaryDirectory() as temp_dir:
            if not self.path.endswith("/"):
                result = asyncio.run(
                    download_file_from_opendal(self.async_op, temp_dir, self.path, file_metadata=self.file_metadata)
                )
                self.downloaded_files.append(result)
            else:
                self.downloaded_files = asyncio.run(download_dir_from_opendal(self.async_op, temp_dir, self.path))

            self.documents = asyncio.run(
                extract_files(
                    self.downloaded_files, file_extractor=self.file_extractor, file_metadata=self.file_metadata
                )
            )

        return self.documents

    def get_document_list(self: Self) -> List[DocumentListItem]:
        """Get a list of all documents in the index. A document is a list are 1:1 with a file."""
        dl: List[DocumentListItem] = []
        try:
            for df in self.downloaded_files:
                dl.append(DocumentListItem(link=df[0], indexed_on=df[2], size=df[3]))
        except Exception as e:
            log.exception("Converting Document list to DocumentListItem list failed: %s", e)

        return dl


class FileStorageBaseReader(BaseReader):
    """File storage reader."""

    def __init__(
        self: Self,
        access_token: dict,
        root: str,
        selected_folder_id: Optional[str] = None,
        path: str = "/",
        file_extractor: Optional[Dict[str, Union[str, BaseReader]]] = None,
        file_metadata: Optional[Callable[[str], Dict]] = None,
        **kwargs: Optional[dict[str, Any]],
        ) -> None:
        """Initialize File storage service reader.

        Args:
            path (str): the path of the data. If none is provided,
                this loader will iterate through the entire bucket. If path is endswith `/`, this loader will iterate through the entire dir. Otherwise, this loader will load the file.
            access_token (dict): the access token for the google drive service
            root (str): the root folder to start the iteration
            selected_folder_id (Optional[str] = None): the selected folder id
            file_extractor (Optional[Dict[str, BaseReader]]): A mapping of file
                extension to a BaseReader class that specifies how to convert that file
                to text. NOTE: this isn't implemented yet.
            file_metadata (Optional[Callable[[str], Dict]]): A function that takes a source file path and returns a dictionary of metadata to be added to the Document object.
            kwargs (Optional dict[str, any]): Additional arguments to pass to the specific file storage service.
        """
        super().__init__()
        self.path = path
        self.file_extractor = file_extractor if file_extractor is not None else {}
        self.supported_suffix = list(DEFAULT_FILE_READER_CLS.keys())
        self.access_token = access_token
        self.root = root
        self.file_metadata = file_metadata
        self.selected_folder_id = selected_folder_id
        self.documents: List[Document] = []
        self.kwargs = kwargs
        self.downloaded_files: List[tuple[str, str, int, int]] = []

    def load_data(self: Self) -> List[Document]:
        """Load file(s) from file storage."""
        raise NotImplementedError

    def get_document_list(self: Self) -> List[DocumentListItem]:
        """Get a list of all documents in the index. A document is a list are 1:1 with a file."""
        dl: List[DocumentListItem] = []
        try:
            for df in self.downloaded_files:
                dl.append(DocumentListItem(link=df[0], indexed_on=df[2], size=df[3]))
        except Exception as e:
            log.exception("Converting Document list to DocumentListItem list failed: %s", e)

        return dl


# TODO: Tobe removed once opendal starts supporting Google Drive.
class GoogleDriveReader(FileStorageBaseReader):
    """Google Drive reader."""

    def __init__(
        self: Self,
        access_token: dict,
        root: str,
        selected_folder_id: Optional[str] = None,
        path: str = "/",
        file_extractor: Optional[Dict[str, Union[str, BaseReader]]] = None,
        file_metadata: Optional[Callable[[str], Dict]] = None,
    ) -> None:
        """Initialize Google Drive reader."""
        super().__init__(
            access_token=access_token,
            root=root,
            selected_folder_id=selected_folder_id,
            path=path,
            file_extractor=file_extractor,
            file_metadata=file_metadata,
        )

    def load_data(self: Self) -> List[Document]:
        """Load file(s) from Google Drive."""
        service = services.google_drive.get_drive_service(self.access_token)
        id_ = self.selected_folder_id if self.selected_folder_id is not None else "root"
        folder_content = service.files().list(
            q=f"'{id_}' in parents and trashed=false",
            fields="files(id, name, parents, mimeType, modifiedTime, webViewLink, webContentLink, size, fullFileExtension)",
        ).execute()
        files = folder_content.get("files", [])
        with tempfile.TemporaryDirectory() as temp_dir:
            self.downloaded_files = asyncio.run(
                download_from_gdrive(files, temp_dir, service)
            )

            self.documents = asyncio.run(
                extract_files(
                    self.downloaded_files, file_extractor=self.file_extractor, file_metadata=self.file_metadata
                )
            )

        return self.documents


class OneDriveReader(FileStorageBaseReader):
    """OneDrive reader."""

    def __init__(
        self: Self,
        access_token: dict,
        root: str,
        selected_folder_id: Optional[str] = None,
        path: str = "/",
        file_extractor: Optional[Dict[str, Union[str, BaseReader]]] = None,
        file_metadata: Optional[Callable[[str], Dict]] = None,
    ) -> None:
        """Initialize OneDrive reader."""
        super().__init__(
            access_token=access_token,
            root=root,
            selected_folder_id=selected_folder_id,
            path=path,
            file_extractor=file_extractor,
            file_metadata=file_metadata,
        )

    def load_data(self: Self) -> List[Document]:
        """Load file(s) from OneDrive."""
        client = services.ms_onedrive.get_client(self.access_token)
        id_ = self.selected_folder_id if self.selected_folder_id is not None else "/drive/root:"
        if client is not None:
            response = client.files.drive_specific_folder(id_, {
                "$select": "id,name,file,size,webUrl",
                "$filter": "file ne null",
                "$top": 100, # Limiting to a maximum of 100 files for now.
            })
            files = response.data.get("value", [])
            with tempfile.TemporaryDirectory() as temp_dir:
                self.downloaded_files = asyncio.run(
                    download_from_onedrive(files, temp_dir, client)
                )

                self.documents = asyncio.run(
                    extract_files(
                        self.downloaded_files, file_extractor=self.file_extractor, file_metadata=self.file_metadata
                    )
                )
        return self.documents


async def download_from_onedrive(files: List[dict], temp_dir: str, client: Any,) -> List[tuple[str, str, int, int]]:
    """Download files from OneDrive."""
    downloaded_files: List[tuple[str, str, int, int]] = []

    for file in files:
        suffix = Path(file["name"]).suffix
        if suffix not in DEFAULT_FILE_READER_CLS:
            log.debug("file suffix not supported: %s", suffix)
            continue
        file_path = f"{temp_dir}/{file['name']}"
        indexed_on = datetime.timestamp(datetime.now().utcnow())
        await asyncio.to_thread(
            services.ms_onedrive.download_file, client, file["id"], file_path
        )
        downloaded_files.append(
            (file["webUrl"], file_path, int(indexed_on), int(file["size"]))
        )

    return downloaded_files


async def download_from_gdrive(files: List[dict], temp_dir: str, service: Any,) -> List[tuple[str, str, int, int]]:
    """Download files from Google Drive."""
    downloaded_files: List[tuple[str, str, int, int]] = []

    for file in files:
        if file["mimeType"] == "application/vnd.google-apps.folder":
            # TODO: Implement recursive folder download
            continue
        suffix = FILE_MIME_EXTENSION_MAP.get(file["mimeType"], None)
        if suffix not in DEFAULT_FILE_READER_CLS:
            continue

        file_path = f"{temp_dir}/{file['name']}"
        indexed_on = datetime.timestamp(datetime.now().utcnow())
        await asyncio.to_thread(
            services.google_drive.download_file, service, file["id"], file_path, file["mimeType"]
        )
        downloaded_files.append(
            (file["webViewLink"], file_path, int(indexed_on), int(file["size"]))
        )

    return downloaded_files


async def download_file_from_opendal(op: Any, temp_dir: str, path: str) -> tuple[str, int, int]:
    """Download file from OpenDAL."""
    import opendal

    log.debug("downloading file using OpenDAL: %s", path)
    op = cast(opendal.AsyncOperator, op)

    suffix = Path(path).suffix
    filepath = f"{temp_dir}/{next(tempfile._get_candidate_names())}{suffix}" # type: ignore
    file_size = 0
    indexed_on = datetime.timestamp(datetime.now().utcnow())
    async with op.open_reader(path) as r:
        with open(filepath, "wb") as w:
            b = await r.read()
            w.write(b)
            file_size = len(b)

    return (filepath, int(indexed_on), file_size)


async def download_dir_from_opendal(
    op: Any,
    temp_dir: str,
    download_dir: str,
) -> List[tuple[str, str, int, int]]:
    """Download directory from opendal.

    Args:
        op: opendal operator
        temp_dir: temp directory to store the downloaded files
        download_dir: directory to download
        supported_suffix: list of supported file suffixes
        file_extractor: A mapping of file extractors to use for specific file types.
        file_metadata: A function that takes a file path and returns a dictionary of metadata to be added to the Document object.

    Returns:
      a list of tuples of 'source path' and 'local path'.
    """
    import opendal

    log.debug("downloading dir using OpenDAL: %s", download_dir)
    downloaded_files: List[tuple[str, str, int, int]] = []
    op = cast(opendal.AsyncOperator, op)
    objs = await op.scan(download_dir)
    async for obj in objs:
        filepath, indexed_on, size = await download_file_from_opendal(op, temp_dir, obj.path)
        downloaded_files.append((obj.path, filepath, indexed_on, size))  # source path, local path

    return downloaded_files


async def extract_files(
    downloaded_files: List[tuple[str, str, int, int]],
    file_extractor: Optional[Dict[str, Union[str, BaseReader]]] = None,
    file_metadata: Optional[Callable[[str], Dict]] = None,
) -> List[Document]:
    """Extract content of a list of files."""
    documents: List[Document] = []
    tasks = []
    log.debug("number files to extract: %s", len(downloaded_files))
    for fe in downloaded_files:
        source_path = fe[0]
        local_path = fe[1]
        metadata = None
        if file_metadata is not None:
            metadata = file_metadata(source_path)

        # TODO: this likely will not scale very much. We'll have to refactor to control the number of tasks.
        task = asyncio.create_task(
            extract_file(Path(local_path), filename_as_id=True, file_extractor=file_extractor, metadata=metadata)
        )
        tasks.append(task)
        log.debug("extract task created for: %s", local_path)

    log.debug("extract file - tasks started: %s", len(tasks))

    results = await asyncio.gather(*tasks)

    log.debug("extract file - tasks completed: %s", len(results))

    for result in results:
        # combine into a single Document list
        documents.extend(result)

    return documents


async def extract_file(
    file_path: Path,
    filename_as_id: bool = False,
    errors: str = "ignore",
    file_extractor: Optional[Dict[str, Union[str, BaseReader]]] = None,
    metadata: Optional[Dict] = None,
) -> List[Document]:
    """Extract content of a file on disk.

    Args:
        file_path (str): path to the file
        filename_as_id (bool): whether to use the filename as the document id
        errors (str): how to handle errors when reading the file
        supported_suffix (Optional[List[str]]): list of supported file suffixes
        file_extractor (Optional[Dict[str, Union[str, BaseReader]]] = None): A mapping of file extractors to use for specific file types.
        metadata (Optional[Dict] = None): metadata to add to the document. This will be appended to any metadata generated by the file extension specific extractor.

    Returns:
        List[Document]: list of documents containing the content of the file, one Document object per page.
    """
    documents: List[Document] = []

    file_suffix = file_path.suffix.lower()

    supported_suffix = list(DEFAULT_FILE_READER_CLS.keys())
    if file_suffix in supported_suffix:
        log.debug("file extractor found for file_suffix: %s", file_suffix)

        # NOTE: pondering if its worth turning this into a class and uncomment the code above so reader classes are only instantiated once.
        reader = DEFAULT_FILE_READER_CLS[file_suffix]()
        docs = reader.load_data(file_path, extra_info=metadata)

        # iterate over docs if needed
        if filename_as_id:
            for i, doc in enumerate(docs):
                doc.id_ = f"{str(file_path)}_part_{i}"

        documents.extend(docs)
    else:
        log.debug("file extractor not found for file_suffix: %s", file_suffix)
        # do standard read
        with open(file_path, "r", errors=errors, encoding="utf8") as f:
            data = f.read()

        doc = Document(text=data, extra_info=metadata or {})
        if filename_as_id:
            doc.id_ = str(file_path)

        documents.append(doc)
    return documents
