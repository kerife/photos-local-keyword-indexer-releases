# Dependencias y avisos de terceros — beta pública

Este documento identifica componentes que forman parte de la app o de su
runtime para que el mantenedor prepare los avisos de distribución correctos. El
proyecto se licencia bajo [MIT](../../LICENSE), pero esa licencia no relicencia,
sustituye ni elimina los términos de componentes de terceros.

| Componente | Versión o pin del proyecto | Uso en la beta |
| --- | --- | --- |
| Sparkle | 2.9.2, lock de SwiftPM | Framework de actualizaciones; deshabilitado en la beta unsigned. |
| PhotoScript | 0.5.3 | Lectura, export temporal y cambios explícitamente confirmados en Fotos. |
| PyObjC frameworks | 12.2.2 | Puentes locales de Photos, MapKit, CoreLocation y Quartz. |
| PyInstaller | 6.x | Empaquetado del helper Python incluido. |
| HTTPX, Pydantic, Typer y Rich | rangos declarados en `pyproject.toml` | Dependencias del helper Python. |
| Ollama y `qwen3-vl` | instalación externa del usuario | No se incluyen ni se redistribuyen en el DMG. |

## Gate de distribución

Antes de publicar una nueva prerelease, el build genera desde el Analysis de
PyInstaller un inventario de las dependencias realmente incorporadas al helper
con sus avisos de licencia bajo `ThirdPartyNotices` dentro del bundle. El
verificador de beta comprueba que cada entrada tenga aviso embebido y que el
inventario coincida con la versión, build y fingerprint del helper montado. La
revisión de release debe conservar además la revisión de origen de la app y la
fecha de generación. No se deben copiar avisos de memoria ni asumir una
licencia por el nombre de un paquete.

La fuente Python declara rangos, no un lockfile con hashes de todas las ruedas.
Por tanto, cada build público debe conservar de forma privada su inventario
resuelto y publicar únicamente el aviso legal y la procedencia que corresponda.
