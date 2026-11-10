# Licensing

This project combines your wrapper code with someone else's model. Two licenses apply to
different parts, and the difference matters in practice.

## At a glance

| Part | License | Commercial use |
|---|---|---|
| Wrapper code: `app.py`, `Dockerfile`, `docker-compose.yml`, `Makefile`, `scripts/`, docs | [MIT](LICENSE) | **Yes**, no permission needed |
| Model code in `underdog_woof/` and the weights `woof_int8.npz` | [Underdog Non-Commercial 1.0](LICENSES/LicenseRef-Underdog-NonCommercial-1.0.txt) | **No** - needs a commercial license from Underdog |
| Audio produced by this service | Yours, but distributed under Underdog Non-Commercial 1.0 | **No** - and credit is required |

MIT applies to the wrapper only. It grants nothing with respect to the model.

## FAQ

**Can I copy `app.py` into my own product?**
Yes. MIT allows commercial use, modification, redistribution and sublicensing. Keep the
copyright notice and the MIT text, and do not expect any warranty.

**Can I run this service commercially?**
Not with the Underdog Woof model. MIT covers the wrapper, but using the model, its weights or
its audio in a paid product, an ad, monetized content, a hosted API or a company's internal
operations needs a commercial license from Underdog: license@conway.tech.

**Can I sell or monetize the generated audio?**
No, unless you hold a commercial license from Underdog. Non-commercial publication is
allowed, and it must credit "Underdog Woof" or "underdog.ai". Apps that play the audio must
show "Voice by Underdog Woof" with a link to underdog.ai - this service already puts that
footer on its index page.

**Can I use the audio to train another TTS model?**
No. The license forbids training, fine-tuning or distilling any other speech or audio model
on the model or its outputs.

**Can I publish the Docker image?**
Yes, but the image contains the weights, so sharing it counts as sharing the model: keep
`LICENSE` and `NOTICE` inside the image unchanged (they are copied in), and say what you
changed - nothing was changed in the model itself. Recipients are bound by the same
non-commercial terms.

**Can I fork the wrapper and swap in another TTS model?**
Yes, that is the intended use of MIT. Once Underdog's weights are out of the picture, only
your new model's license applies.

**Who owns the audio my users generate?**
The users do, to the extent the law allows, but it stays under the Underdog non-commercial
terms unless they buy a commercial license.

## Machine-readable markers

* SPDX license identifiers (MIT) in every file we wrote.
* `REUSE.toml` declares the upstream model files as
  `LicenseRef-Underdog-NonCommercial-1.0` without editing them.
* The image carries `org.opencontainers.image.licenses="MIT AND LicenseRef-Underdog-NonCommercial-1.0"`,
  visible in `docker inspect`.
* `GET /health` reports the same split under `license`.

## Upstream

* Model: <https://huggingface.co/ConwayResearch/Underdog-Woof>
* License: <https://huggingface.co/ConwayResearch/Underdog-Woof/blob/main/LICENSE>
* Commercial licensing: license@conway.tech
