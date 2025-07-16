
How to use this PSI Analyzer package:

1. Unzip all contents to a single folder.
2. Make sure you have Python 3.8+ and Streamlit installed:
   pip install streamlit pandas openpyxl
3. Run the Streamlit app from terminal/cmd:
   streamlit run fixed_streamlit_persistent.py
4. Upload your test Excel (sample provided: PSI_07_Input_Meena.xlsx).
5. Analyze results and download outputs.

- The Streamlit app and calculator will use the most up-to-date logic for all PSIs, including the latest AHRQ-compliant PSI 07.
- All appendix/code set JSONs are provided and used by default.
- For troubleshooting: ensure filenames and paths are unchanged inside the folder.
