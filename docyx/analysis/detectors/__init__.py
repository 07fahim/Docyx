"""Real model backends for the analyzer `detector` seams.

Everything here is **optional**. The core pipeline depends only on PyMuPDF,
pydantic, OpenCV and numpy (§24 keeps it resource-light), and analyzers fall
back to heuristics when no detector is supplied. Importing a module in this
package pulls in a heavy ML stack, so it is never imported by the pipeline —
callers wire it up explicitly:

    from docyx.analysis.detectors.table_transformer import TableTransformerDetector
    pipeline = DocyxPipeline(table_analyzer=TableAnalyzer(detector=TableTransformerDetector()))

Install the extra first:  pip install -r requirements-models.txt
"""
