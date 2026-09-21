# Candidato 0.1.1 (2): evidencia local, no publicación

Estado: **READY_FOR_ACCEPTANCE; no publicado**. Beta pública sin firma Developer ID ni
notarización, con distribución manual y Sparkle deshabilitado. La licencia
del proyecto es MIT. Este documento no acredita aceptación instalada ni
autoriza publicar una prerelease.

## Artefacto local verificado

- DMG: `PhotosLocalKeywordIndexer-0.1.1-2-dev-arm64.dmg`.
- SHA-256: `d091ff4f37aa40430c196d724c405b9aca936c1128bb7e8db75b70f39f563ad3`.
- Versión/build: `0.1.1 (2)`; macOS mínimo declarado: `14.0`; arquitectura: arm64.
- Fingerprint del helper:
  `736e1217cf9eb062337abd92e48843a05c60a438f11ff03fdeb5367f9b984ac3`.
- Fingerprint de fuentes de código/configuración:
  `48e6003ff1c48996533e7f59c4d54b226d2b0e7da9c4cfaeb3c59964b136ca33`.
- El directorio local de entrega es `dist/public-beta-0.1.1-2`. Sólo contiene
  el DMG y su sidecar; no se subió ninguno de ellos.

Desde el directorio de los dos archivos, el comando publicado para comprobar
integridad se ejecutó y devolvió `OK`:

```sh
shasum -a 256 -c PhotosLocalKeywordIndexer-0.1.1-2-dev-arm64.dmg.sha256
```

El fingerprint de código se reproduce desde la raíz del proyecto, sin incluir
artefactos generados, evidencia privada ni este informe:

```sh
(rg --files src packaging app/PhotosLocalKeywordIndexer \
  -g '*.py' -g '*.swift' -g '*.sh' -g '*.zsh' -g '*.plist' \
  -g '*.spec' -g '*.json' -g '*.txt'; \
  printf '%s\n' pyproject.toml app/Package.swift app/Package.resolved LICENSE) \
  | LC_ALL=C sort \
  | while IFS= read -r source_file; do shasum -a 256 "$source_file"; done \
  | shasum -a 256
```

## Resultados del 21 de septiembre de 2026

| Comprobación | Resultado |
| --- | --- |
| Suite Python, Python 3.12.14 | 1,517 pruebas pasadas |
| Suite Swift | 665 pruebas, cero fallos |
| Ruff, código propio (`src tests packaging scripts`) | Pasó |
| Sintaxis Zsh de scripts de packaging | Pasó |
| Contrato offline | `ready`; Developer ID, notarización y TCC siguen `not_checked` |
| Helper final, Python 3.12.14 arm64 / PyInstaller 6.22.3 | Construcción y self-check pasaron |
| Bundle de desarrollo aislado | Construcción y firma ad hoc verificadas; no Developer ID |
| Todos los Mach-O del bundle | Mínimos arm64 compatibles con 14.0 |
| Inventario del helper | 34 componentes; avisos y procedencia embebidos; sin pytest |
| DMG, layout, checksum, docs, inventario y compatibilidad | `READY_FOR_ACCEPTANCE` |
| UI ficticia aislada | Se observaron confirmación, Escape, pausa, ajustes y ventana compacta; QA parcial |

La suite en el nuevo runtime reveló una expectativa antigua del mensaje de
DMG y una pérdida real de wakeup entre guardado prioritario y análisis.
Se corrigieron: la cola conserva su reserva entre pases sin recursión, tiene
regresión determinista de `wait()` y pasó 30 repeticiones del caso, además de
la suite completa y revisión independiente. El runtime Homebrew incompatible
también se reemplazó antes de generar este DMG; no se ocultaron esos fallos.

## Cambios implementados

- Revisión manual como flujo inicial. Piloto autónomo desactivado; consentimiento
  explícito con alcance, opción de descripciones y límite fijo de tres imágenes.
- Recorridos pausados: conservar evidencia, comprobar estado o iniciar un piloto
  nuevo; no se envía una reanudación implícita del recorrido anterior.
- Errores con explicación y siguiente acción; detalles técnicos separados.
- Diagnóstico de soporte opt-in y limitado a datos operativos. Privacidad,
  soporte, MIT y avisos legales incorporados al bundle.
- Inventario derivado del análisis de empaquetado, checksum y verificador de
  beta. La declaración de aceptación está ligada al hash del DMG y no sustituye
  la evidencia humana ni da autorización de publicación.

## Compatibilidad de la construcción

El Python Homebrew disponible y algunas de sus bibliotecas exigían macOS 26.
Ese resultado se descartó como candidato macOS 14; no se rebajaron encabezados
Mach-O. Se preparó Python 3.12.14 arm64 aislado, distribución
python-build-standalone 20260901. El nuevo gate inspecciona la slice arm64 de
todo el código Mach-O del bundle y bloquea mínimos superiores a 14.0.

Un pase de este gate acredita los mínimos declarados en los binarios, no una
ejecución real en macOS 14. El runtime y sus avisos se verifican contra la
distribución exacta, incluidas las bibliotecas estáticas; cuando el proveedor
no declara una versión individual, el inventario la identifica como parte
del build de CPython, sin inventarla.

## Aceptación todavía pendiente

- Cuenta macOS limpia, descarga/copia a Aplicaciones y excepción de Gatekeeper
  sólo para esta app. Nunca desactivar Gatekeeper, SIP o AMFI.
- Prompts frescos de Fotos y Automatización del bundle exacto.
- Dry-run de una foto de prueba y guardado expresamente autorizado, read-back
  independiente, rollback y read-back del rollback.
- Interrupciones durante análisis y escritura, reapertura sin duplicación,
  explicación de estados inciertos y limpieza de temporales.
- VoiceOver, teclado completo, foco tras hojas y Reducir movimiento. Las
  comprobaciones parciales de la interfaz ficticia no sustituyen estas pruebas.
- Soporte público operativo y contacto privado elegido por el mantenedor.
  La comprobación de GitHub del 21 de septiembre encontró Issues deshabilitado.
- Publicación autorizada y posterior redescarga/verificación de los assets.

No se modificó la fototeca real ni se reemplazó la app instalada durante esta
implementación. Developer ID, notarización, stapling y prueba de actualización
entre dos versiones firmadas permanecen como tramo final separado.
