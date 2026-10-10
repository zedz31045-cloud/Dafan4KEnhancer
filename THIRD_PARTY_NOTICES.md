# Third-party notices

Dafan 4K Enhancer uses the components below. Licenses are summarised from each project's public
documentation and **must be re-checked by you before public redistribution**.

| Component | Use | License (as published upstream) |
|---|---|---|
| Real-ESRGAN (xinntao) and its pretrained models | AI upscaling | BSD-3-Clause |
| Real-ESRGAN-ncnn-vulkan | Portable inference executable | MIT |
| ncnn (Tencent) | Inference engine inside the executable | BSD-3-Clause |
| FFmpeg (BtbN "gpl" Windows build) | Video decode/encode | GPL-3.0-or-later (includes libx264/libx265) |
| CustomTkinter | GUI | MIT |
| Pillow | Image I/O | HPND (MIT-CMU style) |
| NumPy | Arrays | BSD-3-Clause |
| opencv-python-headless | Noise reduction | Apache-2.0 |
| tkinterdnd2 | Drag & drop | MIT |
| PyInstaller | Packaging | GPL-2.0 with bootloader exception |

## Notes
- Real-ESRGAN, FFmpeg and the models are **downloaded at build time** from their official GitHub release
  pages (see `tools_manifest.json`); they are not stored in this repository.
- The release ZIP contains an FFmpeg **GPL** build. If you distribute that ZIP you must comply with the GPL
  (keep `tools/ffmpeg/LICENSE-ffmpeg.txt` and offer the corresponding source, e.g. via the BtbN
  FFmpeg-Builds repository). To avoid this, point `tools_manifest.json` to an LGPL build and
  note that libx264/libx265 would then be unavailable.
- Ensure you have the right to process the images and videos you enhance.
