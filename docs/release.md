# Publicación de la app macOS

La futura distribución oficial será un DMG para Apple Silicon y macOS 14 o posterior. La app no usa App Sandbox: necesita acceso a Fotos mediante PhotoKit y automatización de Fotos mediante Apple Events. Ollama y sus modelos no se incluyen en el DMG.

## Estado del checkout

La beta pública gratuita
[`v0.1.0-beta.1`](https://github.com/kerife/photos-local-keyword-indexer-releases/releases/tag/v0.1.0-beta.1)
incluye el DMG arm64 y su checksum SHA-256. Es explícitamente una beta de desarrollo ad hoc: no está firmada con
Developer ID, notarizada ni validada por Gatekeeper como distribución oficial. El repositorio público contiene solo
los assets de descarga y notas saneadas; no contiene manifests, imágenes, captions, coordenadas, logs o rutas locales.

La publicación oficial continúa bloqueada hasta completar identidad Developer ID, configuración de Sparkle, perfil
de notarización y smoke firmado. Un bundle local `READY_DEV` o la beta pública nunca deben describirse como una release
oficial mientras esos gates sigan pendientes. `READY_DEV` designa el canal técnico ad hoc, no una autorización de
distribución: un artefacto `READY_DEV` sin versionar es solo local. Un DMG
nombrado puede entrar al canal de beta pública únicamente después de pasar el
verificador de beta, tener checksum, inventario de dependencias y evidencia de
aceptación correspondientes, y recibir autorización explícita de publicación.
La prerelease actualmente publicada `v0.1.0-beta.1` está limitada por sus
notas a una **beta pública unsigned con actualización manual**, no a una
release normal de Gatekeeper; un candidato posterior no hereda ese estado.

Para el smoke local de un bundle `READY_DEV`, ábrelo desde Finder o Terminal.app fuera de un host agente sandboxed.
Un proceso iniciado directamente por ese host puede heredar restricciones que impidan consultar LaunchServices;
si AppKit no obtiene el ASN de la aplicación, aborta en `_RegisterApplication` antes de mostrar la ventana. Este
diagnóstico es independiente de la firma: abrirlo localmente no convierte el bundle de desarrollo en distribuible.

Los builds de desarrollo usan firma ad hoc por defecto. Como esa firma tiene un requisito designado basado en un
hash que cambia al recompilar, macOS puede considerar la app o su helper como clientes TCC nuevos y volver a pedir
acceso a Fotos o Automatización. Para un smoke local repetible, una identidad de firma local ya provisionada puede
seleccionarse explícitamente:

```bash
LOCAL_CODE_SIGN_IDENTITY='<identidad local instalada>' \
  BUILD_ROOT="$PWD/build/app-local-stable" \
  packaging/build_swift_app.sh
```

El script comprueba la identidad antes de eliminar o crear el bundle y firma con ella el helper, Sparkle, el binario
principal y la app exterior. No solicita timestamp y el resultado sigue marcado `READY_DEV`: sirve únicamente para
pruebas internas en esta Mac. `LOCAL_CODE_SIGN_IDENTITY` se rechaza en builds Release; una distribución normal sigue
requiriendo `DEVELOPER_ID_APPLICATION`, notarización y los demás gates de release.

## Modelos Ollama externos y límite del preflight

Los modelos son una dependencia local externa al producto. `OLLAMA_MODELS` debe
configurarse en el proceso que inicia el servidor de Ollama, nunca en la app ni
en el helper incluido. Por ejemplo, con el volumen ya montado:

```bash
OLLAMA_MODELS="/Volumes/Modelos/Ollama" OLLAMA_NO_CLOUD=1 ollama serve
```

Si Ollama se inicia como aplicación desde Finder, una variable exportada en
una terminal no se hereda automáticamente. Configura el entorno del agente o
servicio que inicia Ollama y reinícialo; después confirma con `ollama list` que
el modelo requerido aparece con su nombre exacto. El disco debe estar montado
en cada ejecución. Si no lo está, el servidor puede responder pero el
preflight reportará el modelo como ausente; monta el volumen y repite el
preflight antes de ejecutar cualquier `ollama pull` manual. La app no crea,
mueve, elimina ni reubica modelos.

La ruta debe ser la raíz del almacén de Ollama (la que contiene `blobs` y
`manifests`), no una de esas carpetas. En un host de release que ejecute Ollama
desde Terminal, el ejemplo completo es:

```bash
OLLAMA_MODELS="/Volumes/Modelos/Ollama" OLLAMA_NO_CLOUD=1 ollama serve
```

Si el equipo usa la aplicación de Ollama desde Finder, establece el entorno de
la sesión de lanzamiento antes de abrirla, o el proceso no verá el volumen:

```bash
launchctl setenv OLLAMA_MODELS "/Volumes/Modelos/Ollama"
launchctl setenv OLLAMA_NO_CLOUD "1"
open -a Ollama
```

Confirma con `ollama list` que el proceso ya ve el modelo; no retires las
variables durante el arranque. Cuando cierres Ollama y ya no necesites el
volumen, retira esa configuración de la sesión:

```bash
launchctl unsetenv OLLAMA_MODELS
launchctl unsetenv OLLAMA_NO_CLOUD
```

Monta el volumen y ejecuta `ollama list` antes de probar el helper. Ningún
script de release configura esa variable ni inicia Ollama; el helper solo usa
el endpoint local fijo.

`OLLAMA_NO_CLOUD=1` es la configuración recomendada del servidor para hacer
explícita la política local; la app además usa únicamente el endpoint de
loopback y rechaza nombres de modelos cloud. No se deben configurar hosts,
proxies ni rutas de modelo alternos en esta aplicación.

El `release_preflight.sh` es deliberadamente independiente de este estado: no
lee `OLLAMA_MODELS`, no inicia Ollama, no descarga modelos y no inspecciona el
disco externo. Solo comprueba que el artefacto de release y sus herramientas
sean válidos. La comprobación del servidor ocurre en el preflight de ejecución
de la app, mediante `127.0.0.1:11434`; por eso un `READY` de release no prueba
que el volumen esté montado ni que un modelo esté instalado.

## Requisitos del equipo de release

- Xcode actual con las herramientas de línea de comandos.
- Python 3.12 arm64 y las dependencias de build instaladas con `python3.12 -m pip install -e '.[build]'`.
  El script valida la arquitectura del intérprete seleccionado antes de ejecutar
  PyInstaller; si informa `x86_64`, configura `PYTHON_BIN` con la ruta de una
  instalación arm64 de Python 3.12 y repite el build.
- Una identidad válida de Developer ID Application configurada en el llavero de la máquina de release.
- Un perfil local previamente configurado para `notarytool`.
- La clave pública Ed25519 de Sparkle ya está provisionada en `packaging/sparkle-config.json`; su clave privada permanece exclusivamente en el llavero del equipo de release, bajo la cuenta local específica del proyecto. El feed sigue siendo una plantilla: la build publicable exige una URL HTTPS real (no los dominios reservados `example.invalid`, `example.org`, `example.com`, `example.net` ni sus subdominios) mediante `SPARKLE_FEED_URL`, junto con la clave pública mediante `SPARKLE_PUBLIC_ED_KEY`.
- Sparkle está fijado exactamente a `2.9.2` en `app/Package.swift` y a la revisión `6276ba2b404829d139c45ff98427cf90e2efc59b` en `app/Package.resolved`. El lockfile se conserva en el bundle fuente para que el entorno de release no pueda seleccionar otra revisión accidentalmente. La resolución local de SwiftPM puede seguir requiriendo un Xcode/toolchain compatible.
- El contrato offline y el locator del build normalizan la identidad y el
  origen de Sparkle sin distinguir mayúsculas/minúsculas y exigen un único pin;
  un duplicado como `sparkle`/`Sparkle` bloquea el release de forma consistente.
  Ambos rechazan además un `Package.resolved` symlinked; el contrato offline
  exige que sea un archivo regular y acotado antes de anunciar `ready`.
- `build_swift_app.sh` aplica ese rechazo también antes de invocar SwiftPM, para
  que un lockfile externo no llegue a iniciar una compilación que después será
  descartada por el locator de Sparkle.
- Además, valida el pin exacto de Sparkle (origen, identidad, versión y revisión)
  antes de SwiftPM; así un `Package.resolved` bien formado pero cambiado no
  puede iniciar una compilación que luego se descarte. El fallo estable es
  `build_swift_app:FAIL:sparkle_lock_invalid` y no se elimina el bundle previo
  porque la limpieza destructiva comienza después de este preflight.
- El lockfile también está limitado a 1 MiB en el locator usado por el build;
  un archivo regular sobredimensionado se rechaza antes de leerlo o iniciar
  SwiftPM, evitando una entrada accidental o manipulada que vuelva el build
  impredecible.
- Si el JSON del lock usa una forma heredada inválida o el documento raíz no es
  un objeto, el locator lo rechaza como `pins list` ausente sin propagar un
  traceback de Python.
- `build_swift_app.sh` usa `--disable-automatic-resolution`; si el checkout no tiene las dependencias ya resueltas, la build falla localmente en lugar de contactar un repositorio o seleccionar otra revisión.
- Las pruebas SwiftPM usan el mismo target de la app y, por tanto, requieren
  que Sparkle 2.9.2 esté disponible en el checkout o en la caché local de
  SwiftPM. `swift test --disable-automatic-resolution` nunca descarga nada:
  si falta Sparkle o el toolchain no es compatible, falla cerrado. No se usa un
  stub ni una dependencia condicional en el manifiesto de release, porque eso
  cambiaría la resolución y el producto que se firma. En ese caso los tests
  SwiftPM se reportan como no ejecutados, no como verdes.
- Tras validar el Xcode seleccionado, `build_swift_app.sh` resuelve el compilador
  mediante `xcrun --find swift` con `DEVELOPER_DIR` (cuando está definido) y usa
  esa ruta absoluta. Así una instalación donde `PATH` aún apunta al Swift de
  Command Line Tools no puede producir una build con un toolchain distinto del
  que pasó el preflight.
- Después de SwiftPM, el script valida con `lipo -archs` el ejecutable principal
  que va a copiar al bundle y exige exactamente `arm64`; también rechaza una
  ruta de ejecutable symlinked. Un binario stale o de otra arquitectura falla
  antes de crear el bundle, incluso en builds de desarrollo.
- Cada release debe recibir un `APP_VERSION` semver de tres componentes y un `BUILD_NUMBER` entero positivo nuevo y creciente; ambos quedan embebidos en `Info.plist` para que Sparkle reconozca futuras actualizaciones.

## Construcción y firma

Antes de iniciar un release, ejecuta el diagnóstico de solo lectura. No firma,
sube ni modifica Ollama o Fotos; devuelve `READY` únicamente cuando todos los
gates están disponibles. El perfil de notarización es obligatorio: el script
consulta `notarytool history` para comprobar sus credenciales; esta validación
no sube artefactos, pero sí requiere acceso al servicio de notarización de
Apple:

```bash
APP_VERSION='<versión semver>' BUILD_NUMBER='<build positivo>' \
  DEVELOPER_ID_APPLICATION='<identidad de firma>' \
  APPLE_NOTARY_PROFILE='<perfil local>' \
  SPARKLE_FEED_URL='<feed HTTPS real>' \
  SPARKLE_PUBLIC_ED_KEY='<clave-publica-ed25519>' \
  packaging/release_preflight.sh
```

El comando acepta únicamente cero argumentos o `--json`. Cualquier otra forma
termina antes de evaluar gates con `release_preflight:FAIL:invalid_arguments`
y la acción `use_no_arguments_or_json`, sin repetir los argumentos ni imprimir
la ruta del checkout.

El preflight exige esos dos valores porque el build Release los consume de
inmediato: `APP_VERSION` debe coincidir con la versión de `pyproject.toml` y
`BUILD_NUMBER` debe ser un entero positivo. Si falta alguno, informa
`release_metadata_missing`; si su forma o versión no coincide, informa
`release_metadata_invalid`. Ambos casos indican
`set_matching_app_version_and_positive_build_number` sin imprimir los valores.

Para repetir el diagnóstico con otra configuración de actualizaciones, cambia
el feed o la clave en ese comando completo; las asignaciones de una invocación
no quedan exportadas para la siguiente.

Sin esas variables el gate informa `sparkle_update_configuration_missing`;
si el feed no es HTTPS, contiene credenciales, usa uno de los dominios reservados
`example.invalid`, `example.org`, `example.com`, `example.net` (o un subdominio,
sin importar mayúsculas) o la clave no es base64 canónica de 32 bytes, informa
`sparkle_update_configuration_invalid`. El preflight no imprime los valores ni
contacta el feed. Incluso una URL malformada se reduce a ese código estable,
sin exponer un traceback de Python.
La app aplica la misma exclusión case-insensitive al iniciar Sparkle, por lo que
un placeholder no se activa aunque llegue con mayúsculas en el `Info.plist`.

Al generar el appcast, usa siempre la cuenta de llavero registrada en
`packaging/sparkle-config.json`; no dependas del valor predeterminado global de
Sparkle ni exportes la clave privada:

```bash
SPARKLE_KEY_ACCOUNT="$(/usr/bin/python3 -c 'import json; print(json.load(open("packaging/sparkle-config.json"))["keychain_account"])')"
app/.build/artifacts/sparkle/Sparkle/bin/generate_appcast --account "$SPARKLE_KEY_ACCOUNT" \
  --download-url-prefix '<prefijo HTTPS público real>/' \
  /ruta/privada/al/directorio-de-artefactos
```

La generación correcta debe producir un `appcast.xml` con un único item por
build nuevo y un `sparkle:edSignature` no vacío en su enclosure. El directorio
solo puede contener DMG ya firmados, notarizados y stapled; un appcast local de
prueba no convierte un DMG de desarrollo en publicable.

Si devuelve `BLOCKED`, resuelve los códigos `FAIL` mostrados y vuelve a
ejecutarlo. Nunca compartas la salida junto con credenciales o perfiles de
notarización.

El preflight comprueba también `/usr/bin/python3`, que los verificadores de
bundle usan para leer `Info.plist` y validar evidencia. Si falta, emite
`system_python3_missing` y recomienda restaurar las herramientas de línea de
comandos de Xcode; no confundir este intérprete del sistema con el Python 3.12
arm64 usado para construir el helper.

Cada gate fallido emite una línea `release_preflight:HINT:<código>:<acción>`
con el siguiente paso seguro, sin imprimir rutas de llavero, perfiles ni
credenciales. Además, `release_preflight:NEXT:<código>` identifica el primer
gate bloqueante para que un wrapper o una interfaz gráfica pueda enfocar el
diagnóstico. Por ejemplo:

Las acciones se expresan en `snake_case` y son idénticas en la salida humana y
en el JSON; así la CLI y la UI pueden compartir el mismo catálogo sin traducir
variantes del texto.

```text
release_preflight:FAIL:python3.12_missing
release_preflight:HINT:python3.12_missing:install_arm64_python312_and_set_PYTHON_BIN
release_preflight:NEXT:python3.12_missing
```

En el modo JSON, `next_action` conserva ese código por compatibilidad y
`next_action_action` contiene la única acción que debe mostrar la CLI/UI. Ambos
campos representan el mismo primer bloqueo; no se debe elegir una acción de
otro elemento de `hints`. Cuando todos los gates pasan, la pareja es
`run_build_python_helper`/`run_build_python_helper`.

Para adjuntar el diagnóstico a un reporte de soporte o consumirlo desde una
interfaz, usa el modo JSON:

```bash
packaging/release_preflight.sh --json > release-preflight.json
```

El archivo usa `schema_version: 2` y contiene únicamente `PASS`/`FAIL`, códigos,
acciones y el estado `ready`/`blocked`. El campo `status_scope` vale
`release_gates` para dejar claro que ese estado no cubre permisos de runtime;
`runtime_status` vale `not_checked` hasta ejecutar el smoke firmado. También incluye `runtime_checks`, con
`photos` y `automation` en `NOT_CHECKED`: el preflight de release no consulta
el TCC del usuario y no puede confirmar permisos de una instalación concreta.
La acción `run_signed_smoke_test` es la única ruta de confirmación para esos dos
gates. No incluye rutas del checkout o del bundle, variables de entorno,
perfiles, nombres de identidad, stderr de las herramientas ni credenciales.

Para que una interfaz o un reporte humano pueda localizar el bloqueo sin
reconstruirlo a partir de una lista plana, el JSON también contiene
`diagnostic_sections` en este orden fijo: `toolchain`, `signing`,
`notarization` y `runtime`. Las tres primeras tienen estado `ready` o `blocked`
y un arreglo `failed_codes`; la sección `runtime` siempre tiene estado
`not_checked`, enumera `PHOTOS_TCC_RUNTIME_TEST_REQUIRED` y
`AUTOMATION_TCC_RUNTIME_TEST_REQUIRED`, y recomienda
`run_signed_smoke_test`. Un estado `ready` en una sección de build no implica
que TCC esté concedido ni que una aplicación instalada haya sido probada.
El modo JSON conserva el mismo código de salida que el modo humano (0 solo
cuando todos los gates pasan), por lo que un JSON válido o `status: ready` no
debe interpretarse como una instalación con TCC confirmado.

Antes de necesitar una identidad de firma o un perfil de notarización, también
puedes validar el contrato de distribución fuente en cualquier máquina con
Python 3.11+:

```bash
"${PYTHON_BIN:-python3.12}" packaging/verify_offline_release_contract.py --json
```

Este chequeo local verifica la restricción de Python, dependencias críticas,
`AppInfo.plist`, Sparkle `Package.resolved`, entitlements mínimos y presencia de
la cadena completa de scripts de empaquetado: construcción, firma,
notarización, evidencia y los dos helpers `.zsh` que esos scripts cargan.
También compara la dependencia Sparkle declarada en
`Package.swift` con el lockfile y busca en la implementación referencias a
SQLite/osxphotos o rutas `*.photoslibrary`; la clase pública `PhotosLibrary` de
PhotoScript no se considera una ruta privada. Además comprueba que la versión
base de `AppInfo.plist`, `pyproject.toml` y `workflows.py` sea la misma; las
versiones de release deben actualizar primero esa versión de proyecto y
`build_swift_app.sh` rechaza un `APP_VERSION` distinto; el DMG siempre toma su
nombre del `Info.plist` generado. El gate `APP_SETTINGS_PRIVATE_PERSISTENCE`
comprueba además que las opciones de nuevas ejecuciones sigan conectadas al
store privado de Application Support, con directorio `0700`, archivo `0600`,
reemplazo temporal y rechazo de symlinks; no inspecciona ni imprime los valores
guardados. También exige que el `Info.plist` base incluya
descripciones no vacías para Automatización de Apple Events y lectura de Fotos;
sin ellas una instalación limpia no puede presentar correctamente los avisos
de privacidad de macOS. La identidad visual se genera de forma determinista
como `Contents/Resources/AppIcon.icns` desde `build_app_icon.sh`; el `Info.plist`
declara exactamente ese recurso y los verificadores de app/DMG rechazan su
ausencia, symlink o nombre distinto. No abre Xcode, no consulta Apple, no accede a
Ollama, no firma y no modifica Fotos. Sus gates `developer_id`, `notarization`
y `tcc` siempre quedan como `not_checked`; un resultado `status: ready` solo
demuestra que el contrato fuente es coherente. La acción siguiente es ejecutar
`run_signed_release_preflight` en la máquina de release. Si el resultado es
`status: blocked`, `next_action` cambia a `review_offline_contract`: primero se
debe corregir el código `FAIL` local y repetir esta comprobación.
El check `RELEASE_STAGE_MARKERS_SCOPED` protege además que helper, app, firma,
DMG, preflight y evidencia describan su etapa real (`READY_FOR_APP_BUNDLE`,
`READY_FOR_SIGNING_AND_DMG`, `READY_FOR_DMG`, `READY_FOR_NOTARIZATION`, runtime sin comprobar y `RECORDED`) en lugar de volver
a un `READY` genérico que pueda confundirse con distribución completa. También
exige que `notarize:READY_FOR_DISTRIBUTION` aparezca únicamente después de
publicar la evidencia, desmontar el DMG y volver a comprobar que el archivo
stapled y el sidecar conservan la misma identidad y contenido.
La bandera interna de finalización también debe quedar después de esas
comprobaciones, para que cualquier fallo previo conserve activo el cleanup de
la evidencia provisional.
Los fingerprints de DMG y sidecar se calculan sobre descriptores abiertos con
`O_NOFOLLOW`: se compara su metadata antes y después de leer, y nuevamente con
el pathname. Así, sustituir el archivo entre `stat` y lectura no puede mezclar
la identidad de un artefacto con el contenido de otro.
Antes de emitir `RECORDED` o `READY_FOR_DISTRIBUTION`, el script completa y
verifica también la limpieza de mount temporal, resultado privado y lock. Un
fallo emite `notarize:FAIL:final_cleanup_failed` y no anuncia READY, evitando
la combinación contradictoria de marcador exitoso con código de salida 1.
La salida cruda de las eliminaciones se descarta para que un cleanup fallido
temprano no publique rutas del lock, evidencia o temporales.
La misma regla cubre la eliminación inmediata de evidencia provisional cuando
falla el segundo detach; el contrato offline rechaza volver a una limpieza que
publique la ruta del sidecar.
Los verificadores internos de layout, helper y firma conservan sus errores en
stderr, pero suprimen sus marcadores de éxito en stdout. Así, el stream de
notarización solo anuncia `notarize:READY_FOR_DISTRIBUTION` al final.

Si el directorio activo de `xcode-select` es exactamente
`/Library/Developer/CommandLineTools`, el preflight emite
`FAIL:xcode_command_line_tools_active` en lugar de un error genérico. La acción
es instalar/seleccionar Xcode completo, incluso si `xcodebuild -version`
responde correctamente; no se modifica `xcode-select` de forma automática.
El build también respeta `DEVELOPER_DIR` cuando apunta explícitamente a un
Xcode completo; si esa variable apunta a Command Line Tools, se rechaza de la
misma forma. Esto permite usar un Xcode completo sin cambiar la selección
global del host.
Además, inspecciona la presencia local de `/Applications/Xcode.app` sin
seguirlo ni modificarlo: `xcode_full_installation_missing` significa que el
host solo tiene Command Line Tools y la acción es `install_full_xcode`;
`xcode_full_installation_not_selected` significa que Xcode existe pero no está
seleccionado y la acción es `select_installed_xcode`. Estos códigos no prueban
licencia, firma, notarización ni permisos TCC.

Cuando ya existe `build/python-helper/dist/PhotosIndexerWorker`, el preflight
también ejecuta el `--self-check` del helper congelado en un entorno limpio y
emite `PASS:helper_runtime`. Si devuelve un formato distinto, falta el runtime
embebido o no puede arrancar, informa
`FAIL:helper_runtime_invalid` y recomienda reconstruir con
`VERIFY_HELPER=1`; no continúa hacia firma ni notarización. Este gate tampoco
confirma permisos TCC.
Ese `self-check` usa además un directorio temporal privado creado para la
ejecución como `HOME` y `TMPDIR`; el preflight lo elimina y confirma su
ausencia antes de decidir `ready`, y no reutiliza el `/tmp` compartido del
sistema. Una sustitución o fallo de limpieza agrega
`helper_check_cleanup_failed` y bloquea el preflight.

Los hints más importantes son: instalar y seleccionar Xcode cuando falta el
toolchain, instalar Python 3.12 arm64 y sus dependencias cuando falla el
helper, reconstruir el helper con `VERIFY_HELPER=1` si el binario es inválido,
y crear o validar el perfil de `notarytool` cuando el gate de notarización está
bloqueado. Si todos los gates pasan, `NEXT:run_build_python_helper` indica el
siguiente paso operativo y después aparece
`READY:release_gates_available_runtime_not_checked`: el entorno de build está
listo, pero todavía falta el smoke firmado que comprueba TCC.

- Para `notary_profile_missing`, crea un perfil mediante el flujo interactivo
  `xcrun notarytool store-credentials '<nuevo-perfil>'`, configura
  `APPLE_NOTARY_PROFILE` con ese nombre y repite el preflight. No pases la
  contraseña como argumento de shell.
- Para `notary_profile_unavailable`, verifica primero la conexión al servicio
  de Apple con `xcrun notarytool history --keychain-profile "$APPLE_NOTARY_PROFILE"`.
  Si el servicio rechaza las credenciales, crea un perfil nuevo con
  `store-credentials`; no continúes a `notarytool submit` mientras el preflight
  siga bloqueado.

1. Construye el helper privado:

   ```bash
   VERIFY_HELPER=1 PYTHON_BIN=python3.12 packaging/build_python_helper.sh
   ```

   `PYTHON_BIN` debe apuntar al intérprete arm64 de Python 3.12 que contiene
   PyInstaller y las dependencias del proyecto. Si se instalaron dentro de un
   entorno virtual local, usa su ruta explícita, por ejemplo:

   ```bash
   VERIFY_HELPER=1 PYTHON_BIN="$PWD/.venv312/bin/python" packaging/build_python_helper.sh
   ```

   El mismo valor debe pasarse a `packaging/release_preflight.sh --json`; de
   lo contrario el preflight puede inspeccionar otro Python 3.12 y marcar
   `PYINSTALLER_MISSING` aunque el entorno seleccionado sí lo tenga.

   `VERIFY_HELPER=1` es el valor predeterminado y ejecuta el binario congelado
   desde un directorio temporal fuera del checkout, con un entorno limpio que
   no contiene `VIRTUAL_ENV`, `PYTHONPATH` ni `PYTHONHOME`. Este gate evita que
   un import accidental desde el entorno de compilación o desde `src/` oculte
   una dependencia ausente del helper distribuible. No omitas esta verificación
   para una build publicable. Los valores distintos de `0` o `1` se rechazan
   antes de crear el directorio de salida, para que una configuración
   accidental nunca publique un helper sin esta comprobación. Ningún
   `BUILD_ROOT` nuevo se crea hasta que terminan los preflights de Python,
   PhotoScript/PyObjC y `lipo`; un error de entorno no deja directorios de
   compilación vacíos.
   Antes de ejecutar PyInstaller, el script importa PhotoScript y los
   frameworks PyObjC requeridos por el worker (`Photos`, `MapKit`,
   `CoreLocation`, `Quartz` y `httpx` junto con sus dependencias). Si falta alguno, termina con
   `build_python_helper:FAIL:python_runtime_dependencies_missing` y muestra la
   acción para instalar las dependencias de build. También comprueba que `lipo`
   esté disponible para verificar la arquitectura arm64. Si falta, termina
   con `build_python_helper:FAIL:lipo_missing` y no ejecuta PyInstaller ni
   genera un helper parcial nuevo. El resultado anterior de
   `dist/PhotosIndexerWorker` se conserva mientras falle cualquier preflight
   de Python, PhotoScript/PyObjC, PyInstaller o `lipo`; solo se limpia justo
   antes de iniciar una nueva compilación, evitando destruir el último helper
   usable durante un intento que no puede comenzar.
   El proyecto fija PhotoScript 0.5.3 y carga su puente mediante la capa de
   compatibilidad acotada por versión y hash del núcleo. Esa capa sustituye en
   memoria únicamente las dos lecturas de reloj incompatibles con macOS 26 y
   restaura inmediatamente el constructor de AppleScript; no modifica la
   dependencia instalada. Una fuente diferente o cualquier otro error de
   compilación se reporta como un preflight fallido con la acción
   `check_photoscript_and_macos_compatibility_before_retry`; no se presenta
   como un permiso de Automatización ni inicia PyInstaller. Si había un helper,
   la línea `previous_helper_preserved_not_release_ready` confirma que se
   conservó solo para diagnóstico: su fingerprint puede quedar stale y no
   autoriza continuar el release. Valida esta combinación en una máquina de
   release antes de publicar. El contrato
   offline también exige que
   `packaging/PhotosIndexerWorker.spec` conserve `target_arch="arm64"`, para
   detectar una regresión de arquitectura antes de iniciar PyInstaller.
   Si PyInstaller, `lipo` o la verificación aislada falla después de comenzar,
   un `EXIT` trap elimina el bundle parcial; solo se desactiva tras completar
   todas las comprobaciones con éxito.
   El terminal `release_helper:READY_FOR_APP_BUNDLE` significa únicamente que
   el helper está listo para integrarse en la app; no afirma que sea instalable
   o distribuible por separado. Antes de emitirlo, el script confirma que el
   directorio privado usado por la verificación aislada ya no existe; un cambio
   de identidad o una limpieza incompleta termina con
   `build_python_helper:FAIL:final_cleanup_failed` y no anuncia éxito.
   Tanto la ruta verificada como `VERIFY_HELPER=0` desactivan los traps de
   `EXIT`, `INT` y `TERM` antes del terminal; una señal posterior no puede
   borrar el helper después de anunciarlo como listo para integrar.
   El script también aplica el guard común al `BUILD_ROOT` antes de crear
   directorios: rechaza componentes symlinkados aunque apunten dentro de
   `build/`. Asimismo rechaza un `build_root/dist` que sea symlink o un archivo,
   antes de limpiar, crear o reutilizar cualquier salida del helper. Los
   directorios temporales `work` y `spec` reciben el mismo guard para que
   PyInstaller no pueda escribir fuera de `BUILD_ROOT` mediante un symlink
   preexistente.
   Los rechazos por componentes symlinked usan un mensaje estable sin imprimir
   el componente ni la ruta absoluta, también cuando el guard se ejecuta desde
   otros scripts de packaging.
   Si la creación de `dist`, `work` o `spec` falla después de iniciar, un trap
   temporal elimina únicamente los directorios vacíos que esta ejecución creó;
   nunca borra directorios preexistentes ni residuos no identificables.
   Los traps de `EXIT`, `SIGINT` y `SIGTERM` comparten esta limpieza: cancelar
   durante PyInstaller o la comprobación aislada no deja un helper parcial.
   Después de una compilación correcta se guarda dentro del payload un marcador
   privado con un fingerprint determinista de las fuentes del worker
   (`src/photos_indexer/*.py`, `worker_entry.py`, el spec y `pyproject.toml`).
   `build_swift_app.sh`, `build_dmg.sh` y `release_preflight.sh` recalculan ese
   valor y bloquean un helper ausente o desactualizado antes de copiarlo o
   firmarlo; no se usan mtimes, por lo que una compilación vieja no puede
   parecer actual solo por conservar sus fechas.

2. Construye la app SwiftUI en configuración Release. El script genera el bundle y copia el helper si ya existe:

   ```bash
   packaging/build_swift_app.sh
   ```

   Este script comprueba `xcodebuild`, que el developer directory seleccionado
   no sea solo Command Line Tools, `/usr/bin/sips` y `/usr/bin/python3` antes de
   invocar `swift build`, porque la build genera el icono determinista después
   de compilar. Así, un host sin las herramientas macOS requeridas falla
   temprano y no deja una compilación Swift aparentemente válida pero un bundle
   incompleto.
   El generador escribe primero `AppIcon.icns` en un temporal privado del mismo
   directorio de destino y solo lo publica al finalizar `sips`; esto también
   funciona si el checkout está en un disco externo. Si el proceso falla o se
   interrumpe, el temporal se elimina y no queda un icono parcial que bloquee el
   siguiente intento. También aplica el guard común de `build/` al destino y
   rechaza padres symlinkados antes de crear el temporal, para que una ruta de
   icono no pueda redirigir la escritura fuera del checkout.
   Un destino ausente, relativo o con extensión distinta de `.icns` falla con
   `build_app_icon:FAIL:output_path_invalid` y la acción
   `provide_a_new_absolute_icns_path_under_build`, antes de crear temporales y
   sin imprimir rutas locales.
   La app anterior se elimina después de validar el destino y un `EXIT` trap
   también elimina cualquier bundle parcial si SwiftPM, Sparkle, el icono, la
   copia del helper o la verificación fallan; el trap solo se desactiva cuando
   la app completa terminó correctamente. La misma limpieza se ejecuta ante
   `SIGINT` o `SIGTERM`.

   Para activar Sparkle en una build publicable, proporciona el feed HTTPS y
   la clave pública Ed25519 (la clave privada nunca entra al repositorio):

   ```bash
   RELEASE_BUILD=1 \
   APP_VERSION='0.1.1' BUILD_NUMBER='2' \
   SPARKLE_FEED_URL='<feed HTTPS real>' \
   SPARKLE_PUBLIC_ED_KEY='<clave-publica>' \
     packaging/build_swift_app.sh
   ```

   Sin esas variables, el menú de actualizaciones queda deshabilitado en
   builds locales y no se usa ningún feed de ejemplo.
   Una build Release correcta termina con
   `release_app:READY_FOR_SIGNING_AND_DMG`: el bundle y su configuración están
   ensamblados, pero la firma Developer ID todavía corresponde al paso
   `build_dmg.sh`; este marcador no declara una app distribuible.

   Si el helper no existe, el script termina con error y no genera un bundle publicable.
   También rechaza el directorio fuente o el binario del helper si son symlinks;
   el app solo puede incorporar el artefacto real producido bajo
   `build/python-helper`.
   Después de copiarlo, el script vuelve a comprobar
   `Contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker` dentro del `.app`;
   una copia ausente, no ejecutable o symlinked produce
   `App bundle copy did not produce an invocable embedded helper.` y no anuncia el
   bundle como construido. Además, `VERIFY_EMBEDDED_HELPER=1` (por defecto)
   ejecuta `packaging/verify_embedded_helper.sh` sobre esa copia exacta. El
   diagnóstico arranca el intérprete congelado con `--self-check` desde un
   entorno vacío y confirma `arm64`, `runtime:embedded` y el contrato JSONL;
   no importa PhotoScript, no abre Fotos, no consulta Ollama y no concede TCC.
   Si se necesita omitirlo en una build de desarrollo incompleta, se puede usar
   `VERIFY_EMBEDDED_HELPER=0`, pero nunca se puede omitir en una release:
   `RELEASE_BUILD=1` con ese valor termina antes de SwiftPM con
   `build_swift_app:FAIL:embedded_helper_verification_required`. Los valores
   distintos de `0` o `1` fallan antes de ejecutar SwiftPM o copiar el bundle.
   En una release, el build también valida la clave pública Ed25519 de Sparkle
   como base64 canónica de 32 bytes antes de SwiftPM; una clave inválida produce
   `build_swift_app:FAIL:sparkle_update_configuration_invalid` y no crea ni
   reemplaza el bundle.

   Para diagnosticar posteriormente una app ya construida, ejecuta el mismo
   chequeo de solo lectura:

   ```bash
   packaging/verify_embedded_helper.sh \
     /Applications/PhotosLocalKeywordIndexer.app
   ```

   El verificador exige exactamente una ruta de app. Cero o varios argumentos
   producen `embedded_helper:FAIL:invalid_arguments` y la acción
   `provide_one_app_bundle_path`, sin imprimir el argumento ni la ruta del
   checkout y antes de ejecutar herramientas o el helper.

   Una salida `embedded_helper:READY` confirma el intérprete y la arquitectura
   del helper embebido. Antes de emitir los `PASS` y `READY`, el verificador
   elimina y confirma la ausencia de su directorio diagnóstico privado; un
   cambio de identidad o fallo de limpieza termina con
   `embedded_helper:FAIL:final_cleanup_failed`. `WorkerProcess` valida esa misma ruta exacta bajo
   `Contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker` antes de lanzar
   el proceso y rechaza symlinks, archivos no regulares, no ejecutables o fuera
   del bundle. `embedded_helper:PASS:photos_tcc:NOT_CHECKED` y
   `embedded_helper:PASS:automation_tcc:NOT_CHECKED` son intencionales: este
   diagnóstico no consulta ni infiere ninguno de los dos permisos. El smoke
   firmado es la única confirmación de TCC para PhotoKit y Automatización →
   Fotos; el diagnóstico jamás los modifica.

   El contrato JSONL del helper es deliberadamente mínimo. Los eventos de
   progreso solo contienen estado, UUID abreviado, modelo y conteos; nunca
   contienen imágenes, base64, captions, OCR, coordenadas ni respuestas del
   modelo. Una referencia `manifest` de un evento terminado debe ser absoluta,
   terminar exactamente en `manifest.json` y no puede incluir `.`/`..` ni un
   componente `.exports-*`. El proyector Python y el decodificador Swift
   aplican ambos esta regla antes de que la interfaz pueda abrir el archivo;
   una violación termina como evento inseguro y no se muestra como un run.

   Si no dispones de Developer ID y solo necesitas montar el bundle en la misma
   Mac para comprobar el layout, crea explícitamente un DMG de desarrollo:

   ```bash
   RELEASE_BUILD=0 \
     packaging/build_dmg.sh "$PWD/build/app/PhotosLocalKeywordIndexer.app"
   ```

   Este artefacto sin versionar es solo para pruebas locales: termina con
   `release_dmg:READY_DEV`, no está firmado ni notarizado y no se debe compartir
   como instalación o release. Un candidato nombrado requiere pasar
   `verify_public_beta.py`, conservar su inventario/avisos, checksum y evidencia
   de aceptación, y una autorización explícita antes de poder compartirse como
   beta pública; aun así sigue sin ser una distribución Developer ID. El comando
   de release oficial del paso siguiente requiere una identidad Developer ID real.

3. Crea un DMG nuevo. `build_dmg.sh` firma la app después de comprobar que el
   helper embebido coincide con el helper recién construido, y después verifica
   los binarios y frameworks anidados desde el interior del bundle hacia afuera.
   No ejecutes `sign_app.sh` por separado: la firma cambia el helper embebido y
   haría fallar el gate de frescura previo al DMG. El script aplica una política
   de entitlements separada al helper que ejecuta PhotoScript y a la app
   principal; no uses `codesign --deep` como sustituto de la firma ordenada.
   Al terminar, el firmador interno emite
   `release_signature:READY_FOR_DMG`: confirma únicamente que la app quedó
   firmada y verificada para continuar al DMG. No es un marcador de
   distribución; todavía faltan el DMG firmado, notarización, stapling y
   evidencia.

   `verify_release.sh` valida que cada plist que consume tenga raíz de
   diccionario; un plist XML válido con raíz de array se rechaza con un error
   estable, sin traceback ni publicación de evidencia incompleta.
   Si recibe una ruta ausente, relativa, symlinked o que no corresponde a un
   bundle `.app` real, termina antes de consultar herramientas con
   `verify_release:FAIL:app_bundle_path_invalid` y la acción
   `provide_a_real_app_bundle`; el diagnóstico no incluye la ruta recibida ni
   la del checkout.
   Antes de anunciar que la verificación pasó, elimina y confirma la ausencia
   del directorio privado usado para extraer entitlements. Un cambio de
   identidad o fallo de limpieza devuelve
   `verify_release:FAIL:final_cleanup_failed` sin publicar un éxito previo.

   Antes de invocar `codesign`, el firmador interno recorre el contenido del bundle y
   rechaza archivos hardlinkados (`sign_app:FAIL:bundle_hardlink`) o propiedad
   de otro usuario (`sign_app:FAIL:bundle_ownership`), y rechaza contenido
   escribible por grupo u otros (`sign_app:FAIL:bundle_permissions`). Esto
   evita firmar en sitio un inode compartido, un artefacto que no pertenece a
   la cuenta que ejecuta el release o un bundle modificable durante la firma;
   los nodos core symlinked usan `sign_app:FAIL:bundle_symlink` y el
   diagnóstico no incluye rutas.
   Una ruta de app ausente, relativa, symlinked o que no apunta a un bundle
   real falla primero con `sign_app:FAIL:app_bundle_path_invalid` y la acción
   `provide_a_complete_app_bundle_under_build`; tampoco imprime la ruta del
   checkout ni intenta invocar `codesign`.

   La verificación inspecciona también los entitlements efectivos extraídos del helper firmado. La atribución TCC definitiva debe confirmarse con el smoke test opt-in en una Mac con la app firmada. El script lee la versión y el build del `Info.plist`,
   genera `PhotosLocalKeywordIndexer-<versión>-<build>.dmg` para Release y
   `PhotosLocalKeywordIndexer-<versión>-<build>-dev-arm64.dmg` para desarrollo;
   este último no cumple el patrón que acepta `notarize.sh`, evitando que un
   artefacto local se suba por accidente. El script no sobrescribe
   archivos existentes y limita su salida a `dist/`:

   ```bash
   DEVELOPER_ID_APPLICATION='<identidad de firma>' \
     packaging/build_dmg.sh /ruta/PhotosLocalKeywordIndexer.app
   ```

   La construcción valida `hdiutil imageinfo` antes de anunciar el artefacto.
   Si el contenedor resultante es inválido, termina con
   `build_dmg:FAIL:dmg_image_invalid` y la acción `retry_the_dmg_build`; el
   diagnóstico no afirma que se eliminó un path que el guard de identidad haya
   preservado por seguridad.
   La salida cruda de `hdiutil create` también se descarta: si la herramienta
   falla, solo se publican `build_dmg:FAIL:dmg_creation_failed` y su hint, sin
   rutas del staging ni del artefacto.
   La copia previa con `ditto` está igualmente aislada: un fallo emite
   `build_dmg:FAIL:app_staging_failed`, limpia el staging privado y termina
   antes de invocar `hdiutil`, sin publicar rutas de la app o del staging.
   Si no puede crear el alias `Applications`, falla igualmente antes de crear
   la imagen con `build_dmg:FAIL:applications_alias_failed`; limpia el staging
   y no publica su ruta temporal.
   Antes de firmar la app, valida que la salida permanezca dentro de `dist/`
   y que el nombre versionado del DMG no exista; un destino inválido o una
   colisión falla sin modificar la app de entrada.
   También comprueba que `hdiutil` esté disponible antes de firmar; en una
   máquina sin esa herramienta termina con `build_dmg:FAIL:hdiutil_missing`.
   Comprueba igualmente `ditto` y `ln` (y, en una build Release, `codesign` y
   `spctl`) antes de invocar `sign_app.sh`; si falta una herramienta, la app de
   entrada no se firma y no se crea la reserva ni el staging del DMG.
   Si al bundle le falta `Contents/Info.plist` o el ejecutable principal,
   termina con `build_dmg:FAIL:app_bundle_incomplete` y la acción segura
   `rebuild_the_app_before_creating_a_dmg`; no intenta reparar, firmar ni
   empaquetar una app parcial.
   Una ruta permitida que no contiene un bundle `.app` existente termina con
   `build_dmg:FAIL:app_bundle_path_invalid` y la acción
   `provide_a_complete_app_bundle_under_build`. El diagnóstico no imprime la
   ruta del checkout ni la ruta recibida.
   Los controles `RELEASE_BUILD` y `VERIFY_DMG_LAYOUT` aceptan únicamente `0`
   o `1`. Un valor distinto termina antes de inspeccionar herramientas o
   reservar la salida con `build_dmg:FAIL:release_build_invalid` o
   `build_dmg:FAIL:verify_dmg_layout_invalid` y un hint que identifica el
   parámetro que debe corregirse.
   Una versión que no use `X.Y.Z` o un build que no sea un entero positivo
   falla antes de reservar la salida con `build_dmg:FAIL:app_version_invalid`
   y la acción `fix_app_version_and_rebuild`; el script no deriva un nombre de
   artefacto a partir de metadata inválida.
   Las colisiones de nombre usan un diagnóstico estable sin imprimir la ruta
   absoluta del artefacto, evitando filtrar rutas locales en logs de CI.
   En modo Release, `DEVELOPER_ID_APPLICATION` se valida antes de crear el
   directorio de salida o la reserva del DMG; una identidad ausente no deja
   artefactos ni sidecars temporales.
   Antes de firmar o crear la imagen, adquiere una reserva atómica junto al
   nombre final (`<artefacto>.dmg.reservation`). Esto impide que dos procesos
   concurrentes firmen o sobrescriban el mismo DMG; el proceso propietario la
   libera al terminar, incluso ante error o interrupción. Si queda una reserva
   tras una pérdida de energía, confirma que no haya otro build activo y
   elimínala manualmente antes de reintentar; el script nunca elimina una
   reserva que no adquirió él mismo.
   Si el destino era nuevo y la reserva falla, el cleanup elimina únicamente
   ese directorio si quedó vacío; un directorio de salida preexistente nunca se
   elimina automáticamente.
   También ejecuta por defecto `packaging/verify_dmg_layout.sh` en modo de
   solo lectura: monta el DMG con `-readonly`, comprueba que el volumen tenga
   exactamente el `.app` y el alias `Applications`, valida la versión/build y
   confirma que el helper embebido es invocable. Este gate no firma, no instala
   en `/Applications` y no usa TCC. Para una imagen incompleta de desarrollo
   se puede omitir explícitamente con `VERIFY_DMG_LAYOUT=0`; no se debe omitir
   en un artefacto candidato a release: `build_dmg.sh` lo rechaza con
   `build_dmg:FAIL:dmg_layout_verification_required` antes de crear o firmar
   el DMG.
   Si `hdiutil`, esa validación o la firma fallan después de crear el archivo,
   el script elimina únicamente el DMG parcial recién seleccionado; no toca
   un DMG preexistente. Esto permite repetir el build sin limpiar `dist/` a
   mano.
   Al completar una build Release emite `release_dmg:READY_FOR_NOTARIZATION`:
   el DMG está creado, validado y firmado, pero aún no es distribuible hasta
   completar `notarize.sh`, stapling, Gatekeeper y la evidencia asociada.
   Antes de ese marcador, `build_dmg.sh` elimina y verifica staging y la reserva
   exclusiva. Si esa limpieza falla, emite
   `build_dmg:FAIL:final_cleanup_failed` y no anuncia el DMG como listo, aunque
   conserva el artefacto ya validado para diagnóstico seguro. Los errores
   crudos de `rm` se descartan para no publicar rutas del staging o del
   artefacto.

   Para repetir solo la comprobación de instalación sin reconstruir el DMG:

   ```bash
   packaging/verify_dmg_layout.sh \
     /ruta/absoluta/dist/PhotosLocalKeywordIndexer-0.1.1-2.dmg
   ```

   Una entrada ausente, relativa, symlinked o inexistente falla en modo humano
   con `dmg_layout:FAIL:invalid_arguments` y la acción
   `provide_an_absolute_dmg_under_dist`; el JSON conserva el mismo código y
   acción, y ninguna modalidad imprime rutas locales.

   Una salida `dmg_layout:READY` valida el contenedor y el contenido mínimo de
   un candidato con nombre de release, pero no equivale a una firma válida,
   notarización aceptada o permiso TCC. Para una imagen `-dev-arm64`, el mismo
   gate emite `dmg_layout:READY_DEV` junto con
   `dmg_layout:HINT:development_build:not_for_official_distribution_run_verify_public_beta_before_any_authorized_publication`;
   en JSON conserva `status: ready` solo para el layout y fija
   `next_action: run_verify_public_beta_before_any_authorized_publication`. No compartas ese
   artefacto de desarrollo como release. El verificador de beta, checksum,
   inventario y evidencia siguen siendo gates separados antes de una beta
   pública autorizada. Si se pasa accidentalmente a
   `notarize.sh`, el script falla antes de consultar perfiles o herramientas
   externas con `notarize:FAIL:development_dmg_not_releasable` y dirige al
   mismo preflight/build de release.
   Para automatizar esta comprobación o adjuntarla a un reporte de soporte,
   añade `--json` antes de la ruta. El resultado contiene únicamente
   `schema_version`, `status`, códigos `PASS`/`FAIL`, `next_action` y el nombre
   de la herramienta; no incluye rutas, hashes, nombres de usuario ni stderr.
   `status: ready` solo significa que el layout local del DMG fue validado.
   Si `hdiutil detach` falla durante la limpieza, el verificador emite
   `dmg_layout:WARN:mount_cleanup_failed` y conserva el punto de montaje: no
   intenta borrarlo mientras podría seguir montado. Revisa y desmonta ese
   volumen manualmente antes de repetir la validación; además termina bloqueado
   con `dmg_layout:FAIL:mount_cleanup_failed` y nunca emite `READY`. El DMG
   original no se elimina.

   6. Notariza y adjunta el ticket. El perfil referido debe existir ya en el llavero local; no se guarda ninguna credencial en el repositorio:

   ```bash
   APPLE_NOTARY_PROFILE='<perfil local>' \
     packaging/notarize.sh /ruta/absoluta/dist/PhotosLocalKeywordIndexer-0.1.1-2.dmg
   ```

   Una ruta de DMG ausente, relativa, symlinked o con otra extensión se
   rechaza antes de consultar herramientas con `notarize:FAIL:dmg_path_invalid`
   y la acción `provide_a_release_dmg_under_dist`. El error no imprime la ruta
   recibida ni la ruta del checkout.

   El script rechaza antes de validar o enviar cualquier artefacto cuyo nombre
   no siga exactamente `PhotosLocalKeywordIndexer-<versión>-<build>.dmg`; esto
   evita subir por accidente un DMG de desarrollo o un artefacto etiquetado de
   forma ambigua. Antes de inspeccionar el DMG comprueba que `hdiutil`,
   `codesign` y `xcrun` estén disponibles y que `xcrun` encuentre `notarytool`.
   Además, `notarize.sh` ejecuta `verify_dmg_layout.sh` localmente antes de
   llamar a `xcrun notarytool submit`; un contenedor incorrecto se rechaza sin
   enviarse a notarización. Si `hdiutil imageinfo` rechaza el contenedor, emite
   `notarize:FAIL:dmg_image_invalid` y la acción estable
   `rebuild_and_verify_the_dmg_then_retry`, sin imprimir la ruta. También
   valida `spctl` antes de montar el DMG,
   porque el mismo gate se ejecuta sobre la app montada y sobre el DMG final.
   `verify_release.sh` trata también una URL Sparkle malformada como un fallo
   estable, sin propagar tracebacks del parser.
   Si falta una herramienta, termina sin inspeccionar ni enviar el artefacto y
   emite un código `notarize:FAIL:<código>` junto con un único
   `notarize:HINT:<código>:install_xcode_command_line_tools_then_retry`.
   El montaje local de preflight se desmonta y elimina antes de crear estado de
   notarización o enviar el artefacto. Si ese cleanup falla, el script termina
   con `notarize:FAIL:dmg_mount_cleanup_failed` y no llama a `notarytool`.

7. Comprueba el ticket y realiza una instalación limpia arrastrando el DMG a Aplicaciones:

   ```bash
   xcrun stapler validate /ruta/absoluta/dist/PhotosLocalKeywordIndexer-0.1.1-2.dmg
   ```

   Tras recibir el estado `Accepted` y completar `stapler`, Gatekeeper y la
   verificación del bundle montado, `packaging/notarize.sh` crea junto al DMG
   un sidecar `*.release-evidence.json`. Antes de generarlo, el script
   vuelve a ejecutar `verify_embedded_helper.sh` sobre la copia extraída del
   DMG. Esto comprueba el intérprete congelado, la arquitectura arm64 y el
   contrato JSONL después de la copia al disco; no abre Fotos, no consulta
   Ollama y no concede permisos TCC.
   Si cualquiera de las cuatro rutas requeridas es relativa, ausente o
   symlinked, el escritor termina con
   `release_evidence:FAIL:input_path_invalid` y la acción
   `provide_private_absolute_release_artifacts`, sin imprimir rutas locales ni
   crear el sidecar.
   Al publicarlo correctamente emite `release_evidence:RECORDED`: este estado
   confirma únicamente que el sidecar quedó registrado, no que una ejecución
   independiente del escritor haya declarado el DMG distribuible. Ese veredicto
   requiere completar los gates de `notarize.sh`.
   Una ejecución completa termina con `notarize:READY_FOR_DISTRIBUTION` solo
   después de publicar la evidencia, desmontar correctamente el volumen y
   volver a verificar el fingerprint del DMG stapled; ese es el marcador
   estable para un artefacto publicable, no `RECORDED`. Si el archivo cambia
   durante ese tramo, la evidencia recién creada se elimina y el proceso emite
   `notarize:FAIL:dmg_changed_before_distribution`.
   El sidecar también se fija al publicarse y se revalida antes del marcador;
   si cambia o es sustituido, el proceso falla con
   `notarize:FAIL:release_evidence_changed_before_distribution` y no anuncia
   el artefacto como distribuible.
   En la ruta integrada, `notarize.sh` retiene el mensaje
   `release_evidence:RECORDED` del escritor hasta completar esas
   revalidaciones. Por tanto, un fallo de desmontaje o integridad que retire el
   sidecar tampoco deja en el terminal un estado `RECORDED` ya obsoleto.
   Conserva ambos artefactos juntos: el sidecar vincula el SHA-256 y tamaño del
   DMG final con la versión/build de la
   app, un fingerprint reproducible `sha256-tree-v1` del contenido del bundle
   (nombres relativos, tamaños, hashes de archivos y destinos de symlink sin
   seguirlos), la versión de Sparkle y el identificador de la operación de
   notarización. No contiene credenciales, mensajes de diagnóstico ni rutas
   temporales. El nombre del DMG también debe coincidir con el nombre del
   bundle, su versión y su build (`<app>-<versión>-<build>.dmg`); así se evita
   asociar evidencia válida a un artefacto etiquetado incorrectamente. Un
   nombre no conforme falla antes de consultar herramientas con
   `notarize:FAIL:dmg_filename_invalid` y la acción
   `rebuild_with_version_and_build_filename`. El
   script se niega a sobrescribir evidencia existente y no crea el sidecar si
   Apple no devuelve `Accepted`. Además, `write_release_evidence.sh` rechaza
   bundles incompletos si falta el ejecutable principal, el helper, el icono o
   el binario de Sparkle, o si la identidad de `Info.plist` no corresponde al
   app distribuido; la evidencia nunca convierte un layout parcial o
   incoherente en un release publicable. Si la respuesta JSON de `notarytool`
   tiene una forma inválida, el escritor devuelve un error estable y tampoco
   crea el sidecar. También rechaza un directorio de salida symlinked para
   impedir que la evidencia se escriba fuera del directorio del DMG mediante
   una ruta redirigida. También rechaza cualquier componente symlink en las
   rutas del DMG, del resultado de notarización o del sidecar antes de leer o
   publicar evidencia, evitando redirecciones mediante symlinks intermedios.
   La misma comprobación se aplica al path del app montado antes de leerlo;
   un padre symlinked no puede redirigir el fingerprint hacia contenido fuera
   del artefacto inspeccionado.
   El validador de `Package.resolved` también sustituye excepciones de lectura
   por un diagnóstico genérico, sin repetir rutas locales ni detalles del
   parser.
   Antes de inspeccionar el disco, también rechaza un DMG
   hardlinkado (`dmg_hardlink`): `stapler` modifica el archivo y un hardlink
   compartiría ese cambio con otro artefacto. Copia el DMG a un archivo nuevo
   con inode propio antes de reintentar; el gate no inspecciona, monta ni
   envía el artefacto rechazado. Las entradas JSON/plist malformadas o
   inaccesibles producen mensajes genéricos: los diagnósticos no repiten rutas
   locales ni excepciones crudas. Si ya existe el sidecar, `notarize.sh` sale
   con `notarize:FAIL:evidence_exists` antes de inspeccionar el DMG y no
   imprime la ruta del artefacto.

## Validación de release

- Arranca la app en una Mac sin Python instalado; debe lanzar el helper incluido en el bundle.
- Verifica que la pantalla inicial detecte si Ollama o `qwen3-vl:4b`/`qwen3-vl:8b` no están instalados y muestre solo los comandos manuales necesarios.
- Ejecuta un dry-run en una biblioteca de Fotos de prueba, revisa el CSV y confirma que no contiene imágenes, captions, texto OCR, coordenadas ni rutas temporales.
- Prueba apply, read-back y rollback únicamente con el smoke test opt-in y un UUID autorizado de la biblioteca de prueba.
- Comprueba que la app firma solicita permisos para su identidad propia en Fotos y Automatización, sin pedir Acceso total al disco.
- Publica el appcast de Sparkle por HTTPS y firma cada actualización. Una actualización no puede descargar modelos ni modificar keywords.

El rollback debe documentarse y probarse junto con captions: si el manifiesto registra un caption aplicado por ese run,
solo puede retirarlo cuando la descripción actual coincide exactamente con el valor aplicado. Si fue editado después, lo
conserva y marca `uncertain` para revisión manual; los captions preexistentes o preservados nunca se modifican. Esta
comprobación no amplía el alcance de la mutación: el único cambio permitido sigue siendo el caption aprobado y las keywords
registradas por el manifiesto revisado.

### Evidencia y gates de este checkout

La validación local automatizada cubre contratos y límites, no sustituye una
instalación firmada en una Mac de release:

| Gate | Evidencia local | Estado que permite publicar |
| --- | --- | --- |
| Contrato fuente offline | `"${PYTHON_BIN:-python3.12}" packaging/verify_offline_release_contract.py --json` | `status: ready`; los gates de firma, notarización y TCC siguen `not_checked` |
| Prerrequisitos del equipo | `packaging/release_preflight.sh` (solo lectura) | Salida `release_preflight:READY:release_gates_available_runtime_not_checked`; todavía falta el smoke firmado de TCC |
| Layout del DMG e instalación simulada | `packaging/verify_dmg_layout.sh /ruta/absoluta/dist/<app>-<versión>-<build>.dmg` (montaje `-readonly`) | `dmg_layout:READY` para un candidato o `dmg_layout:READY_DEV` para desarrollo; ninguno implica por sí solo firma, notarización ni TCC |
| Python, política y workflows | `pytest`, cobertura crítica, Ruff y `compileall` | Comandos verdes en el entorno de release |
| Scripts de empaquetado | `zsh -n packaging/*.sh packaging/*.zsh` y `tests/test_release_hardening.py` | Sin errores de sintaxis ni artefactos incompletos |
| Generador de icono | `packaging/release_preflight.sh` comprueba `/usr/bin/sips` antes del build | Salida `release_preflight:PASS:sips`; el build no falla tarde al generar `AppIcon.icns` |
| Arquitectura del helper | `packaging/release_preflight.sh` consulta `lipo -archs` sobre el helper que se va a embebar | Salida `release_preflight:PASS:helper_architecture` y exactamente `arm64` |
| Bundle firmado | `packaging/verify_release.sh <app>` | `codesign`, hardened runtime, Team ID, entitlements y `spctl` verdes |
| DMG notarizado | `packaging/notarize.sh /ruta/absoluta/dist/<app>-<versión>-<build>.dmg` y su sidecar `*.release-evidence.json` | `notarytool`, `stapler validate`, `spctl`, verificación del app montado y sidecar SHA-256 verdes |
| Smoke mutante | `tests/macos_mutation_smoke.py` con allowlist y dos confirmaciones | Solo en una biblioteca de prueba y con salida `smoke:passed` |

No se debe interpretar un build local, un parseo Swift o un smoke deshabilitado
como una release firmada/notarizada. Si faltan Xcode, Python 3.12, identidad
Developer ID, Sparkle o el perfil local de `notarytool`, el gate queda
bloqueado y los scripts deben terminar con error.

### Diagnóstico TCC y smoke

macOS no expone un chequeo pasivo público para saber si Automatización → Fotos
está permitida. La app muestra ese estado como pendiente y la primera operación
PhotoScript es la comprobación efectiva. Para `-1743`, concede Automatización
a la identidad que ejecuta la operación (la app instalada en una release; el
proceso ejecutor en el CLI) y vuelve a ejecutar el preflight/scan. No concedas
Acceso total al disco como sustituto.

El smoke real está deshabilitado por defecto y exige simultáneamente Darwin,
un UUID de una sola foto de una biblioteca de prueba,
`PHOTOS_INDEXER_SMOKE_CONFIRM` y
`PHOTOS_INDEXER_SMOKE_CAPTION_CONFIRM`. Sin esas condiciones informa
`smoke:disabled`/`smoke:refused` y no es evidencia de mutación. Atiende
`smoke:cleanup_required` manualmente antes de considerar el entorno limpio.
El manifiesto del smoke se crea bajo un directorio temporal efímero y se elimina
al finalizar; el harness no deja `smoke-runs/` ni otros artefactos persistentes
en el checkout. Si el proceso termina abruptamente, comprueba también los
temporales del sistema antes de repetirlo.

## Diagnóstico de distribución

- Si Gatekeeper rechaza el bundle, vuelve a ejecutar la verificación de firma y la notarización; no desactives SIP ni AMFI.
- Si `release_preflight.sh` informa `helper_binary_invalid`, el directorio del
  helper existe pero su ejecutable está ausente, no ejecutable o es un symlink;
  elimina el artefacto de build y vuelve a construirlo con
  `VERIFY_HELPER=1` antes de continuar.
- Si el helper falla fuera del entorno virtual, vuelve a construirlo con Python 3.12 arm64 y comprueba los imports de PhotoScript, Photos y MapKit.
- Si `release_preflight.sh` informa `sips_missing`, el host no expone la herramienta
  macOS que genera el icono determinista del bundle. Ejecuta el release en una Mac
  compatible donde exista `/usr/bin/sips`; no sustituyas el gate con una utilidad
  instalada desde una ruta no controlada.
- `verify_release.sh` también valida al inicio la disponibilidad de `plutil`,
  `lipo`, `codesign` y `spctl`. Si informa que falta una de ellas, corrige el
  entorno de verificación y vuelve a ejecutar el script; no interpretes un
  error de comando ausente como un fallo del bundle.
   Los fallos de sintaxis de `Info.plist` o de entitlements se emiten como
   `verify_release:FAIL:info_plist_invalid` o
   `verify_release:FAIL:entitlements_plist_invalid`, sin repetir rutas ni el
   diagnóstico crudo de `plutil`.
   Los rechazos de symlinks que escapan del bundle y de ejecutables anidados no
   arm64 usan `verify_release:FAIL:bundle_symlink` y
   `verify_release:FAIL:nested_architecture`, también sin rutas.
- Si la app ve Fotos pero devuelve `-1743`, concede Automatización a la app instalada, no al terminal usado durante la compilación.
- Si Ollama no responde, el diagnóstico debe limitarse a `127.0.0.1:11434`; no se ofrecen endpoints alternos ni descargas automáticas.
