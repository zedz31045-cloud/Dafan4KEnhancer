# Dafan 4K Enhancer

برنامج Windows لتحسين الصور والفيديو وتكبير الدقة: واجهة داكنة (CustomTkinter)، تكبير بالذكاء الاصطناعي عبر **Real-ESRGAN (ncnn-Vulkan)**، ومعالجة الفيديو والترميز عبر **FFmpeg**.
Windows app for AI image/video upscaling: dark CustomTkinter UI, **Real-ESRGAN (ncnn-Vulkan)** and **FFmpeg**. Arabic / English UI.

> **تنبيه صريح:** نماذج الذكاء الاصطناعي **لا تستعيد تفاصيل حقيقية** غير موجودة في المصدر؛ التفاصيل الناتجة تقديرية وقد تختلف عن الواقع (خصوصًا الوجوه والنصوص الصغيرة).

## الحالة الحالية (ما تم التحقق منه وما لم يتم)

| البند | الحالة |
|---|---|
| منطق المعالجة (صور، فيديو، طابور، إلغاء، أخطاء، تنزيل، فك ضغط آمن) | اختُبر آليًا على Linux مع **FFmpeg حقيقي** و**محرك Real-ESRGAN وهمي** يحاكي سطر أوامره |
| Real-ESRGAN الحقيقي (نتائج التكبير، GPU، السرعة) | **لم يُختبر** هنا (لا GPU ولا تنزيل نماذج) |
| الواجهة الرسومية (CustomTkinter) | **لم تُشغَّل** هنا (لا Tk/CustomTkinter)؛ فُحصت ساكنًا فقط. أول تشغيل حقيقي سيكون في GitHub Actions (smoke test) وعندك |
| بناء EXE عبر GitHub Actions | **لم يُنفَّذ** بعد؛ سيظهر نجاحه أو فشله في تبويب Actions |
| إصدارات المكتبات المثبتة | اختيرت لتوافق Python 3.12، لكن **لم يُتحقق منها** من PyPI هنا؛ إن رفض `pip` إصدارًا عدّله في `requirements.txt` |

## الرفع إلى GitHub والحصول على EXE

1. أنشئ مستودعًا جديدًا على GitHub (فارغًا).
2. من مجلد المشروع:
   ```bash
   git init && git add . && git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<حسابك>/<المستودع>.git
   git push -u origin main
   ```
3. افتح تبويب **Actions** ← سير العمل **Build Windows EXE** (يبدأ تلقائيًا عند الدفع، أو شغّله يدويًا بـ *Run workflow*).
4. بعد نجاحه افتح التشغيل ← قسم **Artifacts** ← نزّل `Dafan4KEnhancer-windows-x64` (ملف ZIP).
5. فك الضغط وشغّل `Dafan4KEnhancer.exe`. يجب أن يبقى مجلد `tools` بجانب الـ EXE.

إذا فشل البناء: افتح الخطوة الحمراء في Actions؛ الرسائل تبدأ بـ `::error::` وتُرفع ملفات تشخيص باسم `build-diagnostics`.

## ما يفعله سير العمل (`.github/workflows/build-windows.yml`)
1. **test** (Linux): يثبّت FFmpeg والمتطلبات ويشغّل كل الاختبارات.
2. **build** (Windows): Python 3.12 ← المتطلبات ← تنزيل Real-ESRGAN وFFmpeg (لا يُحفظان في git) ← التحقق من وجودهما وتشغيلهما ← PyInstaller ← ZIP ← اختبار ذاتي للـ EXE (`--selftest --selftest-gui`) ← رفع الـ Artifact.

## التشغيل من المصدر
```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python scripts/fetch_tools.py     # أو من داخل البرنامج: الإعدادات ← تنزيل الأدوات الناقصة
python app.py
```
البناء اليدوي: `pip install -r requirements-dev.txt` ثم `pyinstaller --noconfirm Dafan4KEnhancer.spec` ثم `python scripts/package.py`.

الاختبارات: `python -m unittest discover -s tests -v` (اختبارات الفيديو تتخطى نفسها إن لم يوجد `ffmpeg` في PATH).

## أين كل ملف
| المسار | الوظيفة |
|---|---|
| `app.py` | نقطة الدخول (`--selftest` للفحص دون واجهة) |
| `src/dafan/config.py` | الإعدادات (JSON في `%APPDATA%\Dafan4KEnhancer`) |
| `src/dafan/i18n.py` | نصوص العربية والإنجليزية |
| `src/dafan/media.py` | أنواع الملفات، الفحص (Pillow/ffprobe)، حساب الأحجام والدقة |
| `src/dafan/toolchain.py` | العثور على الأدوات، التنزيل مع التقدم والتحقق SHA-256، فك ضغط آمن |
| `src/dafan/engine_image.py` | تحسين الصور (تشويش اختياري ← Real-ESRGAN ← حفظ باسم جديد) |
| `src/dafan/engine_video.py` | الفيديو: وضع AI (إطارات ← Real-ESRGAN ← ترميز على دفعات) ووضع تقليدي (FFmpeg) |
| `src/dafan/jobs.py` | الطابور والخيوط الخلفية (لا تجمّد الواجهة) |
| `src/dafan/history.py` | سجل العمليات والأخطاء |
| `src/dafan/ui/` | الواجهة: النافذة والصفحات والمقارنة قبل/بعد |
| `tests/` | الاختبارات (`helpers.py` يولّد محرك Real-ESRGAN وهميًا وفيديوهات حقيقية) |
| `scripts/` | تنزيل/تحقق الأدوات، الأيقونة، تجميع ZIP |
| `tools_manifest.json` | روابط التنزيل (ويمكن تثبيت SHA-256 فيها) |
| `Dafan4KEnhancer.spec` | إعدادات PyInstaller |
| `tools/` و`models/` | فارغتان في git؛ الأدوات والنماذج تُنزَّل (النماذج تصل داخل `tools/realesrgan/models`) |

## سلوك مهم
- **الأصل لا يُعدَّل ولا يُحذف أبدًا**؛ الناتج يُكتب باسم جديد `name_dafan_2x.png` ولا يستبدل ملفًا موجودًا. الكتابة تتم أولًا في ملف مؤقت ثم يُعاد تسميته.
- **وضع الفيديو التقليدي** تحجيم Lanczos فقط (لا يضيف تفاصيل ولا يُعد ذكاءً اصطناعيًا). وضع **AI** أبطأ بكثير وافتراضيًا يستخدم نموذج `realesr-animevideov3` المُدرَّب أساسًا على الأنمي.
- لا يوجد استيفاء حركة: تغيير معدل الإطارات يكرر/يحذف إطارات فقط.
- التكبير 2× بنماذج x4 يُنتَج بتصغير ناتج 4× (جودة جيدة لكن أبطأ من 2× الأصلي). نموذج `realesr-animevideov3` له ملفات 2×/3×/4× أصلية.
- صور فوق ‎400 ميغابكسل ناتجًا تُرفض لحماية الذاكرة؛ الصور الكبيرة تُعالَج على بلاطات (tile) حسب «استهلاك الذاكرة».
- «صور شخصية» و«قديمة» تضيفان تقليل تشويش (OpenCV) قبل التكبير؛ **لا يوجد نموذج لاستعادة الوجوه**.
- المعالجة محلية ولا تحتاج إنترنت بعد تنزيل الأدوات.

## التراخيص
انظر `THIRD_PARTY_NOTICES.md`. نسخة FFmpeg المستخدمة بترخيص **GPL**؛ توزيع الـ ZIP الذي يحتويها يلزمك بشروط GPL (إتاحة المصدر وإبقاء ملف الترخيص). راجع الشروط قبل أي توزيع علني.
