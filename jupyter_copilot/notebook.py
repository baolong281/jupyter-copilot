from typing import List
import os
import nbformat
from jupyter_copilot.globals import Globals
from typing import Any

class NotebookManager:
    """
    class managing the content of the notebook in memory
    notebook code is stored in an array of strings, each string representing a cell
    on an update we update the cell index in the array
    """

    def __init__(self, path: str) -> None:
        self.path = path
        # remove leading slash for name
        self.name = path[1:] if path.startswith("/") else path
        self.document_version = 0
        self.language = "python"
        self.notebook_cells = self.load_notebook()

        globals = Globals()
        self.logging: Any = globals.logging

        self.logging.debug("[Copilot] Notebook manager initialized for %s", self.path)

    def load_notebook(self) -> List[str]:
        """
        read the content of the notebook into the cells
        only runs on the first sync / when the notebook is opened
        """

        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Notebook {self.path} not found")

        with open(self.path, "r") as f:
            nb = nbformat.read(f, as_version=4)

        code = self.extract_code_cells(nb)

        # if new notebook, code will be empty so just add empty string
        if len(code) == 0:
            code = [""]

        # when a notebook is newly created and never run this information is not available
        if nb.metadata and nb.metadata.kernelspec and nb.metadata.kernelspec.language:
            self.language = nb.metadata.kernelspec.language.lower()

        return code

    def extract_code_cells(self, notebook: nbformat.NotebookNode) -> List[str]:
        """extract code cells from a notebook into a list of strings"""
        return [
            cell.source
            for cell in notebook.cells
            if (cell.cell_type == "code" or cell.cell_type == "markdown")
        ]

    def delete_cell(self, cell_id: int) -> None:
        """deletes a cell id from the array if it exists"""
        if 0 <= cell_id < len(self.notebook_cells):
            self.notebook_cells.pop(cell_id)
        else:
            self.logging.error(f"Cell {cell_id} does not exist")

    def add_cell(self, cell_id: int, content: str) -> None:
        """
        inserts a cell into the array at the given index
        if the cell index is larger than the length, make a blunch of blank cells
        """
        if 0 <= cell_id <= len(self.notebook_cells):
            self.notebook_cells.insert(cell_id, content)
        elif cell_id > len(self.notebook_cells):
            # fill in the gap with empty strings if the cell_id is greater than the length of the array for some reason
            for _ in range(cell_id - len(self.notebook_cells)):
                self.notebook_cells.append("")
            self.notebook_cells.append(content)

    def update_cell(self, cell_id: int, content: str) -> None:
        """index into array and update the cell content if it exists"""
        if 0 <= cell_id < len(self.notebook_cells):
            self.notebook_cells[cell_id] = content
        else:
            self.logging.error(f"Cell {cell_id} does not exist")

    def get_full_code(self) -> str:
        """return the full code of the notebook as a string"""
        return "\n\n".join(self.notebook_cells)

    def get_absolute_line_num(self, cellId: int, line: int) -> int:
        """
        given cellid and line of the current cell, return the absolute line number in the code representation
        this sort of sucks but it works
        """
        return (
            sum([len(cell.split("\n")) for cell in self.notebook_cells[:cellId]])
            + line
            + cellId
        )

    def set_language(self, language: str) -> None:
        """
        closes and opens the lsp server with the new language
        this runs whenever a notebook is initially loaded
        """
        self.language = language
        self.logging.debug(f"[Copilot] Language set to {language}")

    def update_path(self, path: str) -> None:
        """
        sends a close signal to the lsp server and then opens a new one
        this runs whenever a notebook is initially loaded
        """
        self.path = path
        self.name = path[1:] if path.startswith("/") else path

        self.logging.debug(f"[Copilot] Path changed to {self.path}")


