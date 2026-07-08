Meegeleverde lettertypen (PDF-generatie)
========================================

DMSans-Regular.ttf, DMSans-Bold.ttf, DMSans-Italic.ttf, DMSans-BoldItalic.ttf

DM Sans — het huisstijl-lettertype van de applicatie (zelfde font als de web-UI).
Ontworpen door Colophon Foundry / Google Fonts, verspreid onder de SIL Open Font
License 1.1. Zie: https://fonts.google.com/specimen/DM+Sans
Dit is de PRIMAIRE PDF-font: _registreer_pdf_fonts() laadt deze set als eerste
(onder de interne familienaam "Arial"), zodat offertes en facturen exact dezelfde
typografie hebben als de app. Valt terug op systeem-Arial / DejaVu als de bestanden
ontbreken.

DejaVuSans.ttf, DejaVuSans-Bold.ttf, DejaVuSans-Oblique.ttf,
DejaVuSans-BoldOblique.ttf

DejaVu Fonts — vrij verspreidbaar onder een permissieve, op de Bitstream
Vera Fonts gebaseerde licentie (compatibel met de SIL Open Font License).
Zie: https://dejavu-fonts.github.io/  (License / "Fonts are (c) Bitstream").

Deze fonts worden door de applicatie gebruikt als platform-onafhankelijke
fallback voor PDF-generatie wanneer DM Sans noch een systeemlettertype (Arial op
Windows, Liberation/DejaVu op Linux, Arial op macOS) wordt gevonden. Hierdoor werkt
de PDF-export op Windows, Linux, Docker en cloud-hosting zonder hardcoded fontpaden.
