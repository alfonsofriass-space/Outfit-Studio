# Contexto del Proyecto: Outfit MVP Backend

## Objetivo del MVP
Crear un flujo de backend que permita:
1. Recibir una descripción textual de un outfit.
2. Convertir esa descripción en un JSON estructurado y un prompt refinado para la generación de la imagen.
3. Validar si la descripción del usuario es suficiente (2 piezas distintas, con al
   menos una no accesoria, o 1 prenda fuerte con algún atributo visual explícito).
4. Usar un modelo de fallback (`gpt-5.4-mini`) solo cuando la salida del modelo principal
   (`gpt-5.4-nano`) sea inválida o incumpla criterios objetivos de calidad.
5. Generar 1 imagen tipo "outfit board" (sin modelo, fondo claro, estilo e-commerce) usando `gpt-image-2`.
6. Permitir que la app revise el JSON y el prompt exacto antes de confirmar la llamada
   de imagen.
7. Devolver y listar las imágenes generadas junto a su descripción y prompt asociado.
8. Ofrecer por cada composición una vista puesta opcional, generada con ese PNG como
   referencia y solo tras mostrar su prompt y coste aproximado.
9. Buscar una prenda persistida en tiendas conocidas, con coste previo, una acción
   explícita, resultados cacheados y sin inventar similitud ni disponibilidad.
10. Permitir registro e inicio de sesión locales, separar los outfits por cuenta y
    conservar una vista técnica global para el administrador.
11. Ofrecer una segunda vía de entrada: a partir de una situación descrita por el
    usuario, proponer tres outfits completos entre los que elegir, sin eliminar la vía
    de descripción directa.

## Validación en Dos Capas (decisión posterior al diseño inicial)

### 1. Pre-LLM: filtro de ruido, no aplicación del contrato (revisado 2026-08-27)
- Diccionario fijo de keywords por categoría (`upper`, `lower`, `one_piece`, `footwear`, `accessory`), con normalización de minúsculas y tildes.
- El matching usa palabras completas y plurales controlados; nunca subcadenas
  (`mono` no coincide con `monocromático`).
- Cada nombre principal cuenta como pieza aunque comparta categoría o no tenga una
  conjunción: `camiseta debajo de chaqueta` son dos piezas. Los alias que funcionan como
  subtipo se agrupan: `chaqueta bomber`, `pantalón cargo` y `chaqueta americana` son una
  sola pieza.
- **Único rechazo local:** el texto no contiene ni una prenda reconocida ni un atributo
  visual (`NO_CLOTHING_SIGNAL`). Todo lo demás se marca suficiente y lo decide el modelo.
- Motivo del cambio: el diccionario es cerrado y siempre tendrá huecos. Cualquier
  rechazo por recuento puede equivocarse, porque una palabra desconocida podría ser
  justamente la prenda que falta — `kaftán bordado y sandalias` reconocía una sola pieza
  y se rechazaba pese a describir un outfit completo. El caso de señal cero es el único
  en el que ninguna palabra desconocida podría cambiar el veredicto.
- Coste del cambio: una descripción con señal de ropa pero insuficiente pasa a costar
  una llamada a nano (~$0.0002) en vez de $0. A cambio desaparece la clase entera de
  falsos rechazos por vocabulario. Ninguna de estas descripciones escribe en base de
  datos ni genera imagen: el gasto adicional se limita al texto.
- `evaluate_minimum_info` conserva `recognized_items` y `only_accessories` para el log,
  y distingue `multiple_items`, `strong_item_with_detail` y `deferred_to_model` como
  motivos observables.
- Objetivo: no gastar tokens con inputs vacíos o absurdos, sin afirmar nunca que falta
  una prenda que el usuario sí escribió.

### 2. Post-LLM: fallback decidido por código, no por el modelo
`validation.py` aplica reglas objetivas sobre la salida de nano:
- Si `status: needs_clarification` → **NO** hay fallback (mini recibiría el mismo texto vago; no se gasta la segunda llamada)
- ≥2 items con `certainty: low`
- JSON inválido, `visual_phrase_en` vacía o cualquier incumplimiento del schema Pydantic
- **No** activa fallback un outfit que no supera el contrato mínimo (revisado
  2026-08-27). Una extracción bien formada con pocas prendas es una lectura fiel de un
  texto con pocas prendas: `mini` leería el mismo texto y devolvería lo mismo, igual
  que con `needs_clarification`. Ese caso es terminal y `process_outfit_request` pide
  una aclaración concreta, en vez de gastar una segunda llamada y acabar en `502`.

**Nota importante:** esto sustituye el enfoque original de que nano se auto-evaluara vía
`fallback_recommended`, `fallback_reason` o `status: needs_fallback` en su propio JSON. Un modelo pequeño
autoevaluando su incertidumbre es poco fiable; delegar la decisión al código es más
determinista, barato y testeable. La salida solo admite `ok` o `needs_clarification`.

### Estrategia de fallback a mini
Reintento limpio: se llama a `gpt-5.4-mini` con el mismo `user_description` original y el mismo system prompt, sin pasarle la salida fallida de nano (para no contaminar el contexto).

Desde 2026-07-15, el fallback está endurecido:
- Solo una salida no parseable o que incumple los criterios objetivos de calidad puede
  activar `mini`.
- Autenticación, permisos, rate limit, conexión, timeout y errores 5xx de OpenAI no
  activan otro modelo: sufriría el mismo fallo y duplicaría la latencia.
- `mini` se llama como máximo una vez y su salida también se valida. Si sigue siendo
  inusable, el flujo termina con un error controlado.
- Una respuesta `needs_clarification` de cualquiera de los modelos es terminal y válida;
  nunca provoca una llamada adicional.
- El endpoint traduce indisponibilidad operativa a HTTP `503` y respuestas rechazadas,
  inválidas o inusables a HTTP `502`, sin exponer detalles internos del proveedor.

## Prompt de imagen: compuesto en CÓDIGO, no por el LLM (adaptativo)
Decisión posterior al experimento de la tanda 1: con muchos elementos, la prosa libre del LLM
no garantizaba el orden corporal del board (prendas "en fila"). Ahora:
- El LLM devuelve por cada item una **`visual_phrase_en`** (frase visual corta en inglés, solo
  con atributos presentes en el texto). Es obligatoria y no puede estar vacía. Ya NO
  devuelve `image_prompt` ni `visual_constraints`.
- `styling_notes_en` conserva únicamente relaciones expresadas por el usuario, como una
  falda sobre un pantalón, una bufanda usada como cinturón o un único par de zapatos
  desparejados. Una instrucción vacía invalida la extracción; si no hay relaciones, la
  lista queda vacía.
- `app/prompts/image_prompt_builder.py` compone el prompt final de forma determinista. Con
  1-3 piezas usa una silueta vertical sencilla; desde 4 usa una composición ancha con capas
  superiores separadas de exterior a interior, eje de parte inferior/calzado y rail derecho
  de accesorios (cap de 4). Medias y prendas equivalentes se enrutan a legwear aunque el LLM
  las haya clasificado como accesorios.
- El prompt enumera las piezas exactas del board antes del layout y ambos bloques de
  restricciones prohíben explícitamente añadir nada que no esté listado. La geometría escala
  con el contenido: la banda superior y el rail se redactan en singular cuando contienen una
  sola pieza, y el rail ocupa 16%, 20% o 24% del lienzo según haya uno, dos o tres o más
  accesorios. Sin esto, una zona holgada inducía prendas y complementos inventados.
- Sin relaciones explícitas se conserva la prohibición absoluta de solapes. Cuando existen,
  se muestran al usuario y solo ellas pueden modificar la separación o zona por defecto;
  las dos prendas deben seguir siendo reconocibles y sus atributos legibles.
- El builder falla cerrado si recibe un objeto construido sin validar y sin frase visual;
  nunca degrada silenciosamente a campos crudos ni llama a imagen con un prompt incompleto.
- El prompt compuesto se guarda en `outfits.image_prompt`. Si el análisis se creó con
  `generate_image=false`, la primera llamada posterior usa exactamente ese prompt. Solo
  cuando ya existe una imagen se añade la directiva de variación ("alternative composition
  of the exact same garments") para no re-muestrear la misma composición.

## Restricciones de Imagen
- **Tipo:** Outfit board, collage de moda, catálogo e-commerce.
- **Prohibido:** Personas, cuerpos, rostros, manos, perchas, maniquíes, muebles, decoración o logos no mencionados explícitamente.
- **Características:** Prendas completas y bien visibles sobre fondo blanco o gris claro,
  ordenadas como se visten en un cuerpo. Permanecen separadas salvo una superposición o
  colocación pedida explícitamente por el usuario.
- Estas restricciones viven como constantes en `image_prompt_builder.py`
  (`STYLE_BLOCK`, `CONSTRAINTS_BLOCK` y la variante para relaciones explícitas), no en
  el system prompt del LLM.
- Este contrato sigue siendo exclusivo de la imagen principal. La **vista puesta** es un
  segundo tipo de imagen persistido aparte, solicitado y cobrado por separado; transforma
  la composición en un maniquí neutro sin relajar silenciosamente las restricciones del board.

## Coste y Pricing
Ver **[`pricing.md`](./pricing.md)** para tarifas verificadas, coste por outfit, decisiones de pricing y plan de medición. Resumen: OpenAI completo, board `low` a 1024×1024, límite de 3 regeneraciones, ~$0.0066/outfit; la vista puesta opcional añade habitualmente ~$0.015 solo si se confirma aparte.

## Storage de imágenes: disco local en el MVP (fix del bug de URLs caducas)
Las URLs que devuelve OpenAI **caducan (~1h)**. NO se debe guardar esa URL en BD. Solución MVP: pedir `b64_json`, decodificar y guardar el PNG en `app/generated/`. En BD se guarda la ruta/URL **propia**, nunca la de OpenAI. Migrar a Supabase Storage / S3 cuando el MVP se valide.

## Base de Datos (SQLite + SQLAlchemy) — implementado
- **Motor MVP:** SQLite local (`sqlite:///outfit.db`), vía SQLAlchemy. Migrar a Postgres/Supabase = solo cambiar `DATABASE_URL`.
- **Evolución del esquema:** Alembic es la única vía de migración de la aplicación.
  Una base nueva usa `alembic upgrade head`; una base anterior con el mismo esquema
  inicial se adopta una vez mediante `alembic stamp 20260716_0001` y después se actualiza
  con `alembic upgrade head`. `create_all` queda limitado a fixtures aisladas de test.
- **Integridad SQLite:** cada conexión activa `PRAGMA foreign_keys=ON`. La FK
  `images.outfit_id` usa `ON DELETE CASCADE`; al eliminar un outfit directamente en base
  de datos también desaparecen imágenes, vistas puestas y reservas dependientes.
  `DELETE /outfits/{id}` coordina esa cascada con la retirada posterior de sus PNG
  gestionados y se bloquea si existe una generación visual activa.
- **Modelo:** `users` (1) → `outfits` (N) → `images` (N); cada `image` puede tener 0 o 1
  `worn_view`. Una fila en `images` por cada flat-lay generado; la primera es la original.
  - `users`: id, username único, password_hash Argon2, role, is_active, created_at.
  - `outfits`: id, owner_id (FK), user_description, outfit_json, image_prompt,
    text_model, created_at.
  - `images`: id, outfit_id (FK), path (ruta propia, NO la de OpenAI),
    generation_prompt (prompt exacto usado; nullable para datos históricos), image_model,
    quality, size, cost_estimate, created_at.
  - `worn_views`: id, source_image_id (FK única), path, generation_prompt, image_model,
    quality, size, cost_estimate, created_at. Vive aparte para no consumir ni alterar
    regeneraciones del flat-lay.
  - `regeneration_leases`: outfit_id (PK/FK), token, acquired_at. Reserva efímera;
    solo existe mientras una petición de regeneración está activa.
- **Nº de regeneraciones** de un outfit = `max(COUNT(images WHERE outfit_id=X) - 1, 0)`.
- **Límite:** `MAX_REGENERATIONS = 3` (bloquea al llegar a 4 imágenes) → devuelve `regeneration_limit_reached`.
- **Regeneración:** reutiliza el `image_prompt` guardado y solo re-llama a `gpt-image-2`; **NO** vuelve a llamar al modelo de texto.
- **Ownership:** las rutas API siempre crean con propietario. Un usuario normal filtra
  por su `owner_id`; el administrador puede mantener todos los outfits. Las migraciones
  asignan al admin los datos históricos y la comprobación ocurre antes de pagar.
- Si la imagen original falló, la primera generación posterior se considera la imagen
  original recuperada: devuelve 0 regeneraciones consumidas y 3 restantes.
- La PK de `regeneration_leases` garantiza una sola llamada visual activa por outfit,
  tanto para regeneraciones como para vistas puestas, incluso entre procesos. Una
  petición simultánea recibe HTTP `409` antes de llamar a OpenAI.
  La reserva se libera al terminar o fallar y caduca a los 10 minutos si muere el worker.

## Fallos de generación de imagen (semántica desde 2026-07-08)
- Un fallo de `gpt-image-2` lanza `ImageGenerationError`; **nunca** se persiste una fila de imagen con path falso ni coste fantasma.
- En `/outfits/generate`: el outfit (análisis) se guarda igualmente; respuesta `completed` con `image: null` + `image_error`. El usuario puede regenerar.
- En `/outfits/{id}/regenerate`: HTTP 502 y **no consume** el límite de regeneraciones.
- Una doble pulsación o reintento concurrente recibe HTTP 409 y **no llama** al generador
  de imagen, evitando coste duplicado.
- La vista puesta usa `max_retries=0`: tras un error devuelve `502`, no persiste fila ni
  coste y exige otra acción manual. Un timeout podría haberse procesado y facturado en el
  proveedor, por lo que el backend no promete coste cero ni reintenta por su cuenta.
- Las combinaciones quality/size sin tarifa verificada en `pricing.py` fallan con `ValueError` **antes** de llamar a la API (nunca después de pagar).
- El cap de accesorios del board (`MAX_ACCESSORIES_SHOWN = 4`) se informa al cliente en `accessories_omitted`; no se recorta en silencio.

## Stack Técnico
- **Lenguaje:** Python 3.12+
- **Framework:** FastAPI
- **Validación:** Pydantic
- **ORM / BD:** SQLAlchemy + SQLite (MVP)
- **Servidor:** Uvicorn
- **Integraciones:** OpenAI Python SDK
- **Configuración:** pydantic-settings
- **Sesiones y contraseñas:** cookies firmadas de Starlette + Argon2 mediante pwdlib
- **Migraciones:** Alembic
- **Calidad y testing:** Ruff + pytest
- **CI:** GitHub Actions sobre Python 3.12

## Modelos de OpenAI Configurados
- **Texto Principal:** `gpt-5.4-nano`
- **Texto Fallback:** `gpt-5.4-mini` (solo para salida inválida o extracción que incumple
  los criterios objetivos post-LLM).
- **Búsqueda de productos:** `gpt-5.4-nano` con una o dos acciones `web_search` dentro de
  una petición Responses, mercado España, hasta tres candidatos, allowlist de diez
  dominios y 0 reintentos.
- **Imagen:** `gpt-image-2`; board `low` a `1024x1024` y vista puesta `low` a
  `1024x1536` mediante edición con referencia.

## Endpoints

### 1. `GET /health`
Devuelve el estado de la API (`{"status": "ok"}`).

### Acceso: `/auth/register`, `/auth/login`, `/auth/logout` y `/auth/me`
El registro público crea exclusivamente cuentas con rol `user` y abre la sesión. El
login verifica el hash Argon2 y guarda solo `user_id` en una cookie firmada `HttpOnly`;
logout la limpia y `me` reconstruye el usuario activo. Las rutas de outfits e imágenes
requieren esa sesión. La migración crea el administrador local `admin` / `test`.

### 2. `POST /outfits/generate`
> Idioma: **producto solo-español por ahora** (decisión 2026-07-08). La heurística y los prompts asumen español; el antiguo campo `language` se eliminó del contrato. `user_description` limitada a 500 caracteres.
Procesa la descripción del outfit, la pasa por el modelo de texto y, si
`generate_image=true`, también por imagen. Persiste el outfit y devuelve el estado,
`outfit_id`, `user_description` original, análisis, `image_prompt` exacto,
`flat_lay_estimated_cost` calculado desde la configuración activa, `product_search_items`
e imagen opcional. El frontend personal usa
`generate_image=false` para detenerse en la revisión sin pagar ni persistir una imagen.
`replace_outfit_id` reutiliza un análisis propio que aún no tiene composiciones ni
búsquedas, en vez de abandonarlo al editar la descripción.
Puede devolver un estado `needs_clarification` si la descripción no aporta la información mínima requerida.
Si el servicio de texto no está disponible devuelve `503`; si OpenAI rechaza la petición o
ni el fallback produce una extracción usable, devuelve `502`. En ninguno de esos casos se
genera una imagen ni se escribe un outfit en BD.

### 3. `GET /outfits`
Lista los outfits más recientes (24 por defecto, máximo 100), incluidos los análisis sin
imagen. Acepta `offset` para paginar: el orden desempata por `id` además de por fecha, de
modo que dos outfits creados en el mismo instante no producen huecos ni repetidos entre
páginas. El cliente deja así de recortar en silencio a partir del outfit 25. También
acepta `favourites_only`: el filtro va en la consulta y no en el cliente, porque filtrar
después de paginar solo miraría la página cargada y volvería a esconder outfits. Usa la misma representación completa que el detalle: descripción, extracción,
prompt, modelo de texto, coste actual, accesorios omitidos, composiciones, vistas puestas,
estado de búsqueda por prenda, contador y regeneraciones restantes. Es una lectura local
y no llama a OpenAI.

### 4. `GET /outfits/{outfit_id}`
Recupera esa misma representación para restaurar o continuar el outfit. Devuelve `404`
si no existe. Las composiciones se ordenan desde la original hasta la más reciente y cada
una anida su vista puesta si existe. `product_search_items` muestra la consulta segura o
el detalle que falta y cualquier resultado ya guardado.

### 5. `PATCH /outfits/{outfit_id}`
Cambia los metadatos de archivo: `chosen_image_id` e `is_favourite`, ambos opcionales.
No hay pago detrás, así que no es un endpoint de acción ni usa la reserva por outfit.
Un cuerpo vacío o con un campo desconocido devuelve `422`; una composición que no
pertenece a ese outfit devuelve `409` en vez de aceptarse en silencio; un `null`
explícito en `chosen_image_id` quita la portada elegida.

La elección se guarda como `images.is_chosen`, no como `chosen_image_id` en `outfits`:
esa segunda forma cerraba un ciclo de claves foráneas entre las dos tablas que SQLAlchemy
no puede ordenar y que avisa de que será un error en versiones futuras. El contrato
expone igualmente `chosen_image_id` calculado, y un índice único parcial garantiza como
máximo una composición elegida por outfit. `null` significa que el usuario todavía no ha
elegido: qué miniatura enseñar entonces lo decide el cliente.

### 6. `DELETE /outfits/{outfit_id}`
Elimina el outfit y, mediante cascadas SQLite, sus composiciones, vistas puestas,
búsquedas y reservas. Comparte la reserva exclusiva usada por todas las operaciones
pagadas: devuelve `409` sin eliminar nada si existe una activa, `404` si no existe y
`204` sin cuerpo al completarse. Tras confirmar la transacción retira solo los PNG
referenciados que resuelven dentro de `app/generated/`; tolera archivos ya ausentes y
nunca borra rutas externas.

### 7. `POST /outfits/{outfit_id}/regenerate`
Genera una imagen de un outfit existente reutilizando su `image_prompt` (sin volver a
llamar al modelo de texto). Si todavía no hay imágenes, crea la original con el prompt
revisado exacto y no consume regeneraciones. Si ya existe una, añade la directiva de
variación. Devuelve:
- `regenerated` con la nueva imagen, `generation_prompt`, `regeneration_count` y
  `regenerations_remaining`.
- `regeneration_limit_reached` si ya se alcanzaron las 3 regeneraciones.
- `404` si el `outfit_id` no existe.
- `409` si ya hay una generación en curso para ese mismo outfit; el cliente debe esperar
  a que termine antes de reintentar.
- `409` si un outfit histórico no contiene un prompt visual válido.
- `502` si la generación falla (sin consumir el límite).

### 8. `POST /outfits/{outfit_id}/images/{image_id}/worn-view`
Crea una vista puesta a partir del flat-lay local indicado, usando el análisis persistido
y sin volver a llamar al modelo de texto. Antes de esta acción, las respuestas de imagen
exponen `worn_view_preview` con el prompt exacto y una estimación habitual de `$0.015`.
Tras el primer éxito, repetir la ruta devuelve la misma fila sin coste nuevo. Devuelve
`404` si la imagen no pertenece al outfit, `409` si falta una referencia segura o ya hay
otra operación pagada activa y `502` ante un fallo del proveedor sin reintento automático.

### 9. `POST /outfits/{outfit_id}/items/{item_index}/product-search`
Busca una única prenda usando exclusivamente su extracción persistida y un único
`additional_details` escrito por el usuario, si se aporta. Ese campo admite tanto una
marca como color, material, corte u otros detalles. La validación local rechaza
un tipo genérico o sin atributo diferenciador con `422` antes de crear el cliente. La
petición limita Responses API a entre una y dos acciones web, 0 reintentos, mercado
España, diez dominios, hasta tres candidatos y 4.000 tokens de salida. El prompt mantiene
el tipo de prenda, usa todos los términos descriptivos y ordena las alternativas por
coincidencia con los atributos explícitos; el usuario puede añadir un detalle opcional
incluso cuando la consulta base ya era suficiente. Solo persiste URLs presentes en las
fuentes; el precio
queda como texto bruto cuando aparece y la disponibilidad no se consulta ni garantiza.
Antes de parsear se exige `status=completed`: una respuesta
`incomplete` tiene mensaje propio y cualquier respuesta no utilizable registra en el log
solo identificador, estado, motivo, tipos de salida y uso, sin volcar contenido del
proveedor.

La tabla `product_searches` guarda una fila por intento, con unicidad en
`(outfit_id, item_index, attempt)` e incluyendo los resultados vacíos. Una petición sin
`force_new` devuelve siempre el intento más reciente con `created=false` y sin llamar al
proveedor, de modo que una recarga o un doble clic nunca gastan. Con `force_new=true` se
crea el intento siguiente y se conserva el anterior, porque cada uno fue una llamada
pagada. El tope es `MAX_PRODUCT_SEARCH_ATTEMPTS = 3` por prenda; superarlo devuelve `409`
antes de crear el cliente. El contrato expone `attempts` y `attempts_remaining`. La operación comparte la reserva del outfit con
imágenes y borrado, por lo que una carrera devuelve `409` antes de pagar. Los fallos
operativos devuelven `502` o `503`, liberan la reserva y nunca se reintentan solos.

### 10. `GET /images/{filename}`
Sirve una imagen generada solo si está referenciada por un outfit accesible para la
sesión actual. `url_or_base64` contiene rutas `/images/<uuid>.png`, pero conocer la ruta
no permite descargar una imagen de otro usuario. El administrador puede acceder a todas.

### 11. `POST /outfits/proposals`
Vía de inspiración. Recibe una `situation` y devuelve tres propuestas completas o
`needs_clarification`. La puerta local solo rechaza un texto sin una sola letra
(`evaluate_situation_input`): exigir prendas aquí repetiría el error del diccionario
cerrado sobre un vocabulario todavía más abierto. La ruta se declara antes que
`/outfits/{outfit_id}` para que «proposals» nunca se interprete como identificador.

Persiste **una** fila `proposal_sets` con las tres candidatas y **ningún** outfit: tres
filas por petición dejarían dos análisis huérfanos cada vez. Antes de ofrecerlas se
comprueba que las tres superan `is_outfit_valid`; si alguna no sería generable, la salida
se trata como inusable y entra el fallback una sola vez. Autenticación, permisos, rate
limit, transporte y 5xx nunca activan un segundo modelo. El coste se calcula con
`estimate_text_cost` desde el `usage` devuelto, nunca antes de llamar.

La concurrencia la protege `user_operation_leases`, con `user_id` como clave primaria:
la reserva por outfit no puede cubrir una llamada anterior a que exista el outfit. Una
segunda petición simultánea del mismo usuario recibe `409` antes de pagar, y cada usuario
tiene su propia reserva.

### 12. `GET /outfits/proposals/{proposal_set_id}`
Recupera un conjunto ya pagado sin llamar al proveedor, para reconstruir el paso al
recargar. Devuelve `chosen_indexes` con las posiciones que ya tienen outfit.

### 13. `POST /outfits/proposals/{proposal_set_id}/choose`
Promociona una propuesta a outfit con **cero llamadas pagadas**: la extracción ya está
persistida, así que solo compone el prompt de imagen y escribe la fila. La referencia va
de `outfits` hacia `proposal_sets` mediante `proposal_set_id` y `proposal_index`, con
unicidad sobre ese par. Eso hace idempotente elegir dos veces la misma propuesta y, sobre
todo, deja elegir después otra del mismo conjunto sin volver a pagar: la primera elección
no puede convertirse en un callejón sin salida. El outfit pertenece siempre a quien pagó
las propuestas, aunque lo abra un administrador.

## Estructura de directorios (real)
```text
outfit-app/
├── .github/workflows/ci.yml       # checks independientes de backend y frontend
├── README.md                      # entrada y comandos del conjunto
├── contracts/                     # fixture JSON compartido backend/frontend
├── outfit-app-back/
│   ├── app/                       # API, dominio, prompts y servicios OpenAI
│   ├── experiments/               # runner y salida local ignorada
│   ├── migrations/                # revisiones Alembic
│   ├── tests/                     # 306 tests sin llamadas reales
│   ├── scripts/check.py           # Ruff + formato + pytest
│   ├── .python-version            # Python 3.12
│   ├── contexto_proyecto.md       # arquitectura y roadmap vigentes
│   └── README.md
└── outfit-app-front/
    ├── src/api/                   # contrato mínimo con FastAPI mediante fetch
    ├── src/components/            # cabecera, composición, biblioteca, detalle y búsqueda
    ├── src/types/                 # respuestas tipadas del backend
    ├── src/App.tsx                # flujo y secciones locales sin router ni estado global
    └── README.md
```

Las dos aplicaciones son ejecutables de forma independiente, pero comparten historial,
CI y coordinación en un único repositorio.

## Estado de trabajo y próximos pasos (2026-08-09)

### Últimos bloques completados
- Búsqueda de prendas disponible en el propio resultado de creación:
  `FinalOutfitResponse` incluye `product_search_items`, de modo que la conversión ya no
  exige ir a la biblioteca, abrir la tarjeta y bajar al panel.
- `OutfitRequest.replace_outfit_id` reutiliza un análisis propio sin composiciones ni
  búsquedas al editar la descripción. Antes cada edición abandonaba una fila pagada y
  la biblioteca acumulaba análisis casi idénticos. La reutilización se bloquea si el
  outfit tiene imágenes o búsquedas, porque reescribir `outfit_json` invalidaría los
  `item_index` guardados y borraría el contexto de una imagen ya pagada.
- Búsqueda de prenda repetible: migración `20260828_0008`, columna `attempt` e historial
  por intento. El panel deja siempre visible el campo de detalle, muestra la fecha de la
  búsqueda guardada y avisa de que los enlaces caducan. Antes, una búsqueda completada
  congelaba la prenda para siempre y la única salida era borrar el outfit entero.
- Vocabulario cotidiano incorporado a la heurística previa tras comprobar que
  rechazaba outfits completos y válidos: `jeans`, `bañador`, `chándal`, `tenis`,
  `trench` y `hoodie` como nombres principales, y `americana` como alias para que
  `chaqueta americana` siga contando como una sola prenda. El contrato de producto no
  cambia: una prenda débil aislada y dos accesorios siguen sin bastar.
- Puerta previa invertida: la heurística deja de aplicar el contrato mínimo y solo
  filtra ruido (`NO_CLOTHING_SIGNAL`). El contrato pasa a aplicarlo `is_outfit_valid`
  sobre la extracción del modelo, que sí entiende español. Se elimina así la clase de
  falsos rechazos por vocabulario desconocido, a cambio de ~$0.0002 por descripción
  con señal de ropa pero insuficiente. Sigue sin escribirse en BD ni generarse imagen.
- Mensaje de aclaración previo al LLM reducido a su única causa real; los mensajes de
  recuento se emiten ya solo después de la extracción, donde la cifra es exacta.
- Contrato mínimo de producto cerrado: dos piezas distintas bastan aunque no tengan
  atributos; una sola prenda fuerte exige un detalle visual explícito; una prenda débil
  aislada y dos accesorios no bastan.
- Heurística pre-LLM ampliada para contar nombres principales, incluidas dos prendas de
  la misma categoría y sin conjunción, sin duplicar subtipos como `cargo` o `bomber`.
- Prendas fuertes y mensajes de aclaración alineados antes y después del LLM.
- Los inputs insuficientes nunca llegan a la base de datos ni a una llamada de imagen, y
  nunca se inventan atributos para compensar una descripción pobre. El corte previo a
  OpenAI se limita desde 2026-08-27 al texto sin ninguna señal de ropa.
- Matching exacto con plurales, sin falsos positivos por subcadenas.
- Casos compuestos como `pantalón cargo` y `chaqueta bomber` no se cuentan dos veces.
- Fallback de texto limitado a errores de parseo/calidad; errores operativos nunca llaman
  a `mini`.
- Contrato de extracción reducido: el modelo ya no devuelve certeza global, descripción
  original, `source`, tags, paleta, campos ausentes ni un estado que solicite fallback.
  La certeza por prenda se conserva porque sí participa en la decisión objetiva.
- Salida de `mini` validada, sin posibilidad de una tercera llamada, y errores traducidos
  a HTTP `502/503`.
- Cobertura específica de orquestación, errores del SDK, resultado terminal y endpoints.
- Smoke real del builder v2 completado el 2026-07-15 con 5 casos simples/medios:
  5/5 imágenes generadas, 0 fallbacks, 0 errores, $0.0300 de imagen y coste completo
  estimado de ~$0.033. Las cinco respetaron piezas, atributos explícitos, orden corporal
  y restricciones visuales.
- A/B ciego complejo completado con 12 outfits de 5-8 piezas: 24/24 imágenes,
  0 fallbacks y 0 errores. La lista plana determinista obtuvo 83/84 (98.8%) frente a
  81/84 (96.4%) del builder zonado. Coste completo estimado: ~$0.1512.
- Builder adaptativo implementado: vertical para 1-3 piezas y composición ancha con rail
  semántico desde 4. La regresión real C08/C10 generó 2/2 imágenes, obtuvo 14/14 criterios,
  corrigió el solape kimono/top y redujo el espacio vacío. Coste: $0.012 de imagen y $0 de
  texto al reutilizar las extracciones guardadas.
- Geometría del board proporcional (2026-09-01): un outfit de 4 piezas con un solo
  accesorio producía una camisa y tres complementos inventados, porque heredaba una
  composición afinada sobre casos de 5-8 piezas. Corregido con inventario explícito de
  piezas, redacción en singular y rail proporcional. Validado con mocks y tests de builder;
  sin nueva medición visual real.
- Decisión experimental cerrada: mantener `1024x1024 low`; no probar retrato/`medium`.
- Semántica de regeneración corregida: una primera imagen recuperada no consume una
  regeneración. Reserva atómica por outfit añadida; impide dobles llamadas de pago,
  devuelve `409` ante concurrencia y se recupera tras una caída del worker.
- Fase de reproducibilidad cerrada: `pyproject.toml` declara Python 3.12 y separa
  runtime/dev; Ruff comparte configuración con el repositorio; `scripts/check.py` es
  el único cierre local y de CI; GitHub Actions lo ejecuta sin secretos ni llamadas
  reales a OpenAI.
- Configuración centralizada en un único `Settings`: API key, modelos, timeouts,
  imagen y base de datos se validan antes de aceptar tráfico. Los servicios y el runner
  ya no leen variables de entorno por su cuenta.
- Alembic incorporado con una migración inicial equivalente a los modelos. Se verificó
  upgrade/downgrade sobre una base vacía y adopción por `stamp` sin modificar datos;
  la base local quedó entonces actualizada hasta `20260722_0004`. Las conexiones SQLite
  aplican claves foráneas y la cascada outfit → imágenes quedó verificada sin violaciones.
- Verificación: instalación limpia en `python:3.12-slim`, Ruff en verde y 164 tests
  superados, incluidas carreras con dos sesiones SQLite y pruebas de configuración,
  migraciones y vista puesta. La instalación con la
  versión transitiva actual de Starlette emite un aviso no bloqueante sobre la futura
  transición de `TestClient` a `httpx2`; se abordará al actualizar ese stack.
- Flujo frontend en tres etapas implementado: análisis con `generate_image=false`,
  exposición del prompt exacto, confirmación visual mediante el endpoint existente y
  archivo con texto + prompt por imagen. La primera imagen pendiente no recibe la
  directiva de variación; una variación en curso conserva visible la imagen anterior.
- Migración `20260717_0002` añade `images.generation_prompt` sin reconstruir datos
  históricos. El frontend React/TypeScript vive en `../outfit-app-front` dentro del
  mismo monorepo y usa estado local, `fetch` y CSS propio, sin router ni estado global.
- Backend y frontend consolidados bajo `outfit-app-back/` y `outfit-app-front/` con
  sus historiales preservados y comprobaciones independientes en el CI común.
- Relaciones de estilo explícitas incorporadas al JSON de extracción, al builder y a la
  revisión sin nuevas tablas ni migraciones. El prompt de texto trata un calzado
  desparejado como un único par. La lógica está cubierta sin llamadas reales; la
  comprobación visual específica queda pendiente de un experimento aprobado.
- A/B real de vista vestida cerrado sobre C10: 0 llamadas de texto, 2 de imagen,
  0 reintentos y 0 fallos. La edición con flat-lay de referencia conservó la identidad
  de las seis piezas y fue aceptada por el usuario; costó `$0.014252` frente a `$0.005955`
  de la generación solo desde texto. Se descarta la ruta solo texto y se aprueba
  implementar la edición como segunda acción opcional. Los artefactos permanecen
  locales en `experiments/output/20260720_190229_ab_worn_view/`.
- Vista puesta implementada de extremo a extremo: prompt determinista compartido con el
  experimento, `images.edit` con el flat-lay como referencia, `max_retries=0`, coste por
  `usage` con fallback `$0.015`, migración `20260720_0003`, persistencia 1:1 y archivo
  emparejado. El frontend enseña prompt y coste antes del botón, conserva el flat-lay
  durante errores y no reintenta automáticamente.
- Smoke real de la ruta de producto aceptado el 2026-07-22: reutilizó una composición
  persistida, realizó 0 llamadas de texto, 1 `images.edit`, 0 reintentos y 0 fallos.
  Coste calculado desde `usage`: `$0.014137`. Conservó las tres piezas y sus rasgos
  visuales, creó una única fila 1:1, quedó visible en el archivo y una segunda petición
  devolvió `created=false` sin llamar de nuevo al proveedor.
- Claridad del flujo reforzada sin cambiar arquitectura: la revisión indica `Paso 2 de 3`,
  recibe del backend el coste estimado de la composición, prioriza prendas y relaciones,
  guarda el prompt técnico en un desplegable y reduce el hero. Composición y vista puesta
  usan estados de espera estables de 20-40 segundos, sin progreso inventado, y acciones
  explícitas de reintento tras un fallo.
- Continuidad persistente implementada sin migraciones nuevas: listado y detalle comparten
  un único contrato, incluyen análisis pendientes y reconstruyen imágenes, vistas puestas
  y regeneraciones restantes. React guarda solo `active_outfit_id`, restaura revisión o
  último resultado al recargar y ofrece un listado mínimo para continuar cualquier
  outfit. El contrato se comprueba con `contracts/outfit-detail.v1.json` desde backend y
  frontend.
- Biblioteca visual consolidada sin nuevas tablas ni dependencias: `Crear` y `Biblioteca`
  son secciones locales que conservan el trabajo al alternar. La biblioteca agrupa por
  outfit, usa la primera composición como portada estable y abre un detalle independiente
  con comparación, descargas, regeneraciones, vista puesta y búsqueda de prendas. El logo
  navega al trabajo activo; solo `Nuevo outfit` lo limpia explícitamente. React consume
  únicamente `GET /outfits`; la ruta plana `/outfits/gallery`, su schema y sus componentes
  duplicados siguen retirados.
- P4A implementada sin migraciones ni dependencias nuevas: el borrado usa la misma
  reserva exclusiva que las generaciones, confirma primero la cascada transaccional y
  retira después solo sus PNG gestionados. React exige una confirmación, bloquea acciones
  incompatibles, actualiza el archivo sin recargar y limpia `active_outfit_id` si
  corresponde.
- P5.0 evalúa, sin integrar todavía endpoints ni persistencia nueva, la búsqueda de
  prendas desde `outfit_json`. El runner abre SQLite en modo solo lectura, rechaza
  prendas demasiado vagas y construye consultas solo con atributos persistidos o
  detalles confirmados por el usuario. Se comparó la línea base individual con un modo
  `--batch` que mantiene la identidad mediante `item_id` y limita un outfit completo a
  una acción web. El batch solo cubrió 1/5 prendas, por lo que producto usará búsquedas
  independientes y predecibles por prenda.
- P5.1 cerrada implementa esa estrategia como un corte vertical individual: nueva tabla
  `product_searches`, proyección anidada en el contrato persistente, endpoint por índice
  y panel único dentro del detalle del archivo. Reutiliza la reserva atómica del outfit
  para no solapar búsquedas, imágenes o borrado; persiste también resultados vacíos y
  nunca repite una fila completada. Las suites usan mocks y no ejecutan búsquedas reales.

### Roadmap priorizado actual

#### P0 — Smoke de vista puesta (cerrado el 2026-07-22)

- Se reutilizó una composición local: 0 llamadas de texto, 1 `images.edit`, 0 reintentos
  y 0 errores.
- El coste registrado desde `usage` fue `$0.014137`, por debajo de la previsión de
  `$0.015` y del techo aprobado de `$0.10`.
- Fidelidad visual aceptada por el usuario; fila 1:1, archivo, liberación de reserva e
  idempotencia verificadas. La segunda petición devolvió `created=false` sin coste nuevo.

**Cierre verificado:** la ruta completa funciona con la configuración de producto y no
se necesita repetir el smoke salvo que aparezca una regresión distinta en uso real.

#### P1 — Integridad SQLite y claridad de UX (cerrada el 2026-07-22)

- `PRAGMA foreign_keys=ON` se aplica por conexión y la migración `20260722_0004`
  incorpora `ON DELETE CASCADE` de outfits a imágenes; las dependencias inferiores ya
  conservaban sus cascadas.
- La migración preserva datos existentes y la base local no presenta violaciones en
  `foreign_key_check`. P1 dejó preparada la integridad; la operación de producto se
  pospuso deliberadamente hasta P4.
- El backend devuelve `flat_lay_estimated_cost` desde su configuración verificada.
- El frontend muestra tres pasos, prioriza prendas y relaciones, relega el prompt técnico
  a un desplegable, reduce el hero y usa `composición` y `vista puesta` de forma estable.
- Las esperas indican el rango habitual de 20-40 segundos sin fingir porcentaje. Los
  fallos conservan el estado útil y ofrecen reintentos manuales concretos.

**Cierre verificado:** 164 tests backend, lint/formato, 8 tests frontend y build de
producción, todo sin llamadas reales a OpenAI.

#### P2 — Continuidad persistente (cerrada el 2026-07-27)

- `GET /outfits` y `GET /outfits/{id}` descubren y recuperan tanto análisis pendientes
  como outfits con composiciones antiguas mediante una única representación.
- La respuesta contiene descripción, extracción, prompt, composiciones, vistas puestas y
  regeneraciones restantes; no cambia el esquema SQLite ni realiza llamadas a OpenAI.
- React guarda solo `active_outfit_id` y restaura revisión o último resultado al recargar,
  sin React Router ni estado global. El historial permite abrir cualquier outfit guardado.
- `contracts/outfit-detail.v1.json` es el único fixture que cruza ambos proyectos y se usa
  para comprobar serialización FastAPI y consumo/restauración React.

**Cierre verificado:** validación manual confirmada por el usuario; 169 tests backend,
lint/formato, 14 tests frontend y build de producción, todo sin llamadas reales a
OpenAI.

#### P3 — Archivo como espacio de trabajo (validado el 2026-07-24)

- La biblioteca agrupa por outfit y utiliza la primera composición como portada estable
  en una cuadrícula de tarjetas amplias; los análisis pendientes conservan un estado
  visible sin imagen.
- El refinamiento visual del 2026-07-28 separa `Crear` y `Biblioteca` mediante estado
  local. Cambiar de sección no borra la descripción, revisión o resultado ni requiere
  React Router. El logo navega a `Crear`; `Nuevo outfit` es la única acción de reinicio.
- Abrir una tarjeta sustituye la cuadrícula por un detalle independiente y volver
  devuelve el foco a esa tarjeta. El detalle selecciona la composición más reciente,
  muestra directamente el flat-lay y su vista puesta y evita repetir una única
  composición. Una composición sin vista mantiene un ancho limitado; si existen
  regeneraciones, ofrece un selector compacto para cambiar entre ellas.
- Cada resultado tiene una descarga diferenciada. Una composición histórica sin vista
  permite generarla después de enseñar el prompt exacto y el coste, sin repetir el
  modelo de texto ni habilitar otra operación visual simultánea en la interfaz.
- La navegación permanece segura durante una operación; la cabecera muestra su estado y
  bloquea únicamente acciones destructivas o incompatibles como `Nuevo outfit`.
- El frontend usa únicamente `GET /outfits`; la ruta `/outfits/gallery`, el schema plano
  y las vistas duplicadas se retiraron en el mismo bloque.
- Posponer favorita/elegida hasta observar cuál de las dos semánticas necesita el uso real.

**Cierre verificado:** funcionalidad y refinamientos visuales finales aceptados
manualmente por el usuario; 169 tests backend, lint/formato, 16 tests frontend y build
de producción, todo sin llamadas reales a OpenAI.

El refinamiento de navegación y tarjetas del 2026-07-28 conserva este contrato y se
entrega para una nueva revisión visual manual antes del siguiente bloque de producto.

#### P4 — Mantenimiento local del archivo (P4A cerrada el 2026-07-27)

- P4A implementa `DELETE /outfits/{id}` con cascada de filas y retirada posterior de
  los PNG locales referenciados. La operación comparte la reserva de generación y
  responde `409` sin borrar si hay una imagen en curso.
- El detalle del archivo exige una confirmación irreversible, bloquea acciones
  incompatibles durante la petición, retira el elemento sin recargar y vuelve al
  formulario vacío si era el outfit activo.
- Selección favorita/final, filtros y limpieza automática de disco siguen condicionados
  a una necesidad observada; no forman parte del primer bloque.
- Regresiones visuales dirigidas únicamente para relaciones complejas que fallen en uso.

**Cierre verificado:** borrado y actualización del archivo aceptados manualmente por el
usuario; 186 tests backend, Ruff, 18 tests frontend y build de producción, todo sin
llamadas reales a OpenAI.

#### P5 — Descubrimiento de prendas en tiendas (P5.1 cerrada el 2026-07-28)

- Aproximación deliberadamente textual: la extracción persistida es la fuente de verdad
  y el usuario elige entre tienda, título, precio y enlace. No se compara la composición
  con imágenes de producto ni se calcula un porcentaje de similitud.
- La búsqueda no pide ni muestra miniaturas. Se intentó y se retiró el 2026-09-01: bajo
  `filters.allowed_domains` el proveedor solo devuelve resultados de texto (pricing.md,
  sección 11), así que `thumbnail_url` era siempre nulo de extremo a extremo.
- `experiments.product_search_experiment` admite selecciones por outfit e índice, añade
  únicamente detalles escritos por el usuario y bloquea tipos genéricos o prendas sin
  ningún atributo diferenciador antes de crear un cliente.
- La línea base individual buscó `pantalones palazzo beige`: 1 acción web con cuatro
  consultas internas, 11 fuentes, 5 candidatos respaldados, 4 coincidencias semánticas
  razonables, 1 falso positivo, 0 miniaturas, 14,146 s y `$0.012374` calculados desde
  `usage`. La disponibilidad no se puntúa todavía y los agotados no se descartan.
- Cada request limita el mercado a España y diez dominios conocidos, usa 0 reintentos y
  solo acepta URLs presentes en las fuentes y pertenecientes a la allowlist. El
  experimento individual admitía cinco candidatos; producto lo reduce a tres para una
  selección más legible, sin prometer ahorro relevante por esa reducción.
- El smoke agrupado envió las cinco prendas completas del outfit 5 en un único request:
  1 acción web, 0 extracciones, 0 imágenes, 0 reintentos, 8 fuentes, 8.892 tokens de
  entrada, 418 de salida, 6,667 s y `$0.012301`. El esquema devolvió las cinco entradas,
  pero solo la camisa obtuvo candidato: cobertura **1/5**, sin miniaturas.
- Frente a los `$0.012374` individuales, el batch ahorró `$0.000073` (~0,6 %) y perdió
  cuatro prendas. Se descarta una acción web única para todo el outfit. No se justifica
  otro smoke para tamaños intermedios: añade incertidumbre sin resolver la dependencia
  entre coste y número de acciones web.
- `POST /outfits/{id}/items/{item_index}/product-search` ejecuta esa búsqueda individual;
  `GET /outfits` y el detalle anidan consulta, coste previo y caché. La migración
  `20260727_0005` guarda una única fila por prenda, incluso sin candidatos, y elimina en
  cascada con el outfit.
- El panel aparece una sola vez bajo las imágenes del outfit abierto. Expone la consulta,
  exige detalles adicionales si la extracción es pobre, muestra hasta tres candidatos y
  no inicia ninguna llamada al abrir o recargar. Todas las operaciones pagadas comparten
  la misma reserva por outfit.
- «Buscar outfit completo» podrá reutilizar este flujo como lote explícito, tras mostrar
  el coste total; no forma parte de P5.1. Siguen fuera scraping, comparación visual,
  afiliación, seguimiento de precios/stock y multiproveedor.

**Estado:** estrategia y corte vertical implementados, interfaz validada manualmente;
210 tests backend, Ruff/formato, 27 tests frontend, lint y build sin llamadas reales.
El primer smoke real descubrió y permitió corregir `sources: null`; una repetición
aprobada devolvió y persistió tres candidatos mediante una acción web, 0 reintentos y
`$0.012203` calculados desde `usage`. El usuario confirmó después la caché y presentación
de esos datos reales en la galería; P5.1 queda cerrada.

#### P6 — Dictado opcional de la descripción (cerrada el 2026-08-02)

- El compositor usa `SpeechRecognition` o `webkitSpeechRecognition` cuando el navegador
  lo proporciona, en español y sin dependencias ni llamadas a OpenAI.
- El micrófono añade texto al borrador, nunca lo envía ni inicia el análisis. El usuario
  puede detenerlo y revisar o editar el resultado antes de continuar.
- La interfaz conserva únicamente el icono y su estado visual. En navegadores sin soporte
  se desactiva y se muestra un solo aviso; Firefox mantiene la escritura manual.

#### P7 — Fiabilidad y precisión de búsqueda (cerrada el 2026-08-07)

- **P7A validada:** margen de salida elevado de 1.000 a 4.000 tokens para reducir
  respuestas truncadas sin cambiar el modelo ni las tiendas. El
  backend distingue `incomplete`, respuesta inválida y servicio no disponible, conserva
  diagnóstico seguro y mantiene `max_retries=0`. El frontend muestra una sola explicación
  y deja cualquier nuevo gasto detrás del botón manual de reintento.
- **P7B validada:** los datos reales confirmaron que
  el constructor ya incorporaba tipo, color, material, corte y detalles persistidos. Se
  refuerza el prompt para conservarlos y priorizar las coincidencias más completas, y el
  frontend permite añadir un detalle opcional a cualquier consulta antes del único clic
  de pago. Un smoke real sobre bailarinas negras conservó tipo, color y altura en cinco
  candidatos por `$0.012598`; el detalle `punta` apareció en dos resultados, aunque no en
  las primeras posiciones. No se usa `visual_phrase_en`, no cambia el modelo ni se añade
  otra petición Responses.
- **Ajuste posterior al uso real:** dos respuestas `completed` investigaron una sola
  prenda mediante dos acciones web y eran rechazadas por exigir exactamente una. La ruta
  acepta ahora entre una y dos, mantiene una única petición Responses y 0 reintentos,
  persiste el recuento y calcula el coste observado. El techo previo pasa a `$0.03`. Un
  smoke dirigido reprodujo y aceptó la secuencia de dos acciones: 1 petición, 17 fuentes,
  5 candidatos, 0 reintentos, 10,681 s y `$0.023914` calculados desde `usage`. La calidad
  fue mixta después del primer candidato, por lo que no se amplía el límite ni se presenta
  esta prueba como mejora de precisión.

#### P8 — Cuentas locales, ownership y vistas por rol (cerrada el 2026-09-03)

- Registro e inicio de sesión con nombre de usuario y contraseña desde una única pantalla;
  el registro siempre crea rol normal y no existe creación de admins desde el cliente.
- Contraseñas con hash Argon2 y sesión firmada en cookie `HttpOnly`, sin JWT ni tabla de
  sesiones. La migración crea `admin` / `test` y asigna a esa cuenta los outfits previos.
- Cada cuenta normal solo puede leer o mutar sus outfits e imágenes. El administrador
  conserva acceso global; las referencias ajenas se rechazan antes de cualquier llamada
  de texto, imagen o búsqueda.
- React conserva para admin la vista técnica actual. En cuentas normales oculta prompts,
  modelos, consultas internas y costes, manteniendo creación, biblioteca, descargas,
  vistas puestas y búsqueda de prendas.
- Deliberadamente no hay todavía cuotas ni permisos de gasto: una cuenta registrada puede
  ejecutar las mismas acciones de pago. Este sistema es para uso local, no publicación.

**Estado técnico:** base local migrada a `20260809_0007`, siete outfits históricos
asignados al admin y cero violaciones de claves foráneas; 232 tests backend, Ruff,
33 tests frontend, lint y build en verde, sin llamadas reales a OpenAI.

**Validación manual completada el 2026-09-03.** Se abrió el mismo outfit con las dos
cuentas sobre una copia de la base local y se comprobó el texto renderizado del detalle,
en vez de juzgarlo a ojo:

- La cuenta normal **no ve coste, ni prompt, ni modelo, ni consulta interna**. Conserva
  descarga, vista puesta, búsqueda de prendas, refinado, favorito y portada elegible; los
  precios que sí aparecen son los de la tienda, no los de la API.
- El administrador ve el coste por composición y por vista, los prompts exactos y la
  consulta enviada al proveedor.
- El raíl y el pie cambian de rótulo con el rol, sin cambiar la forma del flujo.

La fase se cierra sin cuotas por usuario, que siguen expresamente aplazadas: separar
datos no es limitar gasto, y por eso la aplicación no debe publicarse con una clave
compartida.

#### P9 — Marca explícita en búsqueda de prendas (cerrada el 2026-09-03)

- `OutfitItem` conserva `brand` únicamente cuando el usuario la menciona; los outfits
  históricos siguen siendo compatibles porque el campo es opcional.
- Antes de buscar se puede mantener, añadir o corregir una marca reconocida desde el mismo
  campo libre usado para el resto de detalles, sin otra llamada de texto ni un input
  separado. El tipo y la marca son requisitos; color, material, corte y detalles ordenan
  resultados de esa misma marca. Una ausencia real produce una lista vacía, no otra marca
  silenciosa.
- Sin marca se conservan las diez tiendas conocidas. Con marca se excluyen las tiendas de
  marcas competidoras, se mantienen Zalando y El Corte Inglés y se antepone el dominio
  oficial reconocido. El primer caso incorporado es `Versace` → `versace.com`.
- La validación local acepta el dominio oficial o exige que título/URL de un distribuidor
  multimarca contengan la marca. Se conserva una petición Responses, hasta dos acciones
  web, cero reintentos y el mismo techo previo.

**Smoke pagado ejecutado el 2026-09-03.** Una búsqueda de `camisa de satén` con `Versace`
desde la propia aplicación: 1 petición, 2 acciones web, 0 reintentos y `$0.023127` frente
al techo de `$0.03`. La consulta conservó la marca como requisito y los atributos
persistidos, y el único candidato vino del dominio oficial `versace.com`. Detalle medido
en `pricing.md` §11.

**Hueco que el smoke no cubre:** Versace sí fabrica camisas de satén, así que no se
ejercitó la ruta de ausencia real. Sigue sin medirse que una marca que no fabrica esa
prenda devuelva lista vacía en vez de colar otra marca; la lógica existe y está cubierta
por tests con mocks, pero no contra el proveedor. Comprobarlo costaría otra llamada.

#### P10 — Inspiración: propuestas a partir de una situación (planificada)

**Decisión de forma.** El compositor conserva el campo de texto actual y añade un
conmutador explícito entre dos vías: `describir` (comportamiento actual, sin cambios) e
`inspiración` (el usuario cuenta la situación y el modelo propone tres outfits). No hay
detección automática de intención: la aplicación nunca elige sola qué llamada pagada
realiza, y el conmutador declara esa llamada antes de hacerla.

**Punto de convergencia.** Las dos vías producen un `OutfitExtraction` y entran en el mismo
paso de revisión. Nada a la derecha de `review` cambia: `build_image_prompt`,
`build_worn_prompt`, la búsqueda de producto, la biblioteca, la reserva por outfit,
`pricing.py`, las migraciones existentes y `contracts/outfit-detail.v1.json`.

**Puerta de validación por modo.** `evaluate_minimum_info` rechaza hoy cualquier texto sin
señal de ropa (`NO_CLOTHING_SIGNAL`), que es exactamente la forma de una situación («boda
de tarde en octubre»). La vía `describir` conserva esa puerta sin tocarla; `inspiración`
usa la suya, que no exige prendas. Es el único punto donde las dos vías divergen dentro del
código que ya existe.

**Proponer no crea outfits.** Una petición de propuestas persiste una única fila
`proposal_set` con los tres candidatos; solo la propuesta elegida promociona a outfit. Se
evita así triplicar los análisis huérfanos que ya se corrigieron al eliminar los callejones
sin salida. El navegador guarda `active_proposal_id` con el mismo criterio que
`active_outfit_id`: un identificador, nunca contenido duplicado.

**Coste y concurrencia.** La generación de propuestas es una llamada de texto detrás de su
propio botón, con el coste mostrado antes. La imagen conserva su confirmación
independiente: la vía añade un paso pagado y no debilita ninguna puerta existente. La
reserva atómica tiene como clave primaria el `outfit_id` y no puede cubrir una propuesta
que todavía no tiene outfit, así que P10 añade un guardia equivalente por usuario para
conservar el invariante de que un doble clic no puede pagar dos veces.

**Modelo.** Se implementa con `gpt-5.4-nano` detrás de configuración. Proponer es una tarea
distinta de extraer y su calidad no se puede validar con mocks: un smoke aprobado decidirá
si escala a `gpt-5.4-mini`. No hace falta tarifa nueva porque ambos modelos de texto ya
están verificados en `pricing.py`.

**Rescate del desajuste.** Cuando la vía `describir` rechaza por `NO_CLOTHING_SIGNAL`, la
respuesta añade `suggested_mode: "inspiration"` en lugar de un error seco, y el frontend
ofrece ahí mismo pedir propuestas. Reutiliza una puerta que ya existe y es el mecanismo
por el que se descubre el modo nuevo. El campo es opcional: no altera el contrato previo.

**Orden de trabajo.**

- **P10A — backend:** prompt de propuestas hermano de `text_system_prompt.py`, endpoint,
  tabla `proposal_set` con su migración, puerta por modo, guardia de concurrencia y tests
  con mocks.
- **P10B — frontend:** conmutador en el compositor, estados `proposing` y `proposals` en
  `FlowState`, pantalla de elección, `active_proposal_id` y las dos vistas por rol.
- **P10C — smoke aprobado:** calidad y coste reales de las tres propuestas; decide entre
  nano y mini y cierra la fase.

**Fuera de alcance.** Armario, perfil de usuario, memoria de gusto entre sesiones,
propuestas por lote y cualquier número de candidatos distinto de tres. La portada elegible,
los favoritos y la paginación de la biblioteca se tratan aparte en P11.

**Estado:** cerrada el 2026-09-03. Las tres subfases completas.

- **P10A (backend) cerrada.** Migración `20260903_0009` con `proposal_sets`,
  `user_operation_leases` y las dos columnas de origen en `outfits`, verificada en
  `upgrade` y `downgrade` sobre una base real. Prompt de propuestas, servicio con la
  misma disciplina de fallback que la extracción, reserva por usuario, puerta de
  situación y los tres endpoints. 280 tests backend en verde, +24 sobre P9, sin ninguna
  llamada real. Dos ajustes sobre el plan escrito: la promoción apunta desde `outfits`
  hacia el conjunto en vez de guardar una única elección en `proposal_sets`, para no
  cerrar las otras dos propuestas; y las propuestas usan su propio
  `openai_proposal_fallback_model`, porque solo esta vía persiste coste y por tanto solo
  ella exige tarifa verificada al arrancar.
- **P10B (frontend) cerrada.** Conmutador de radios nativos en el compositor
  (`Sé lo que quiero` / `Inspírame`) sobre el mismo campo, dictado y geometría; la copia
  de la vía de descripción no cambia. `FlowState` gana `proposing` y `proposals`, que
  convergen en el mismo `review`. `ProposalChoice` presenta las tres opciones con el
  lenguaje del lienzo: número fantasma, titular en display y prendas como filas
  separadas por filete. `active_proposal_id` recupera al recargar un conjunto ya pagado.
  El aviso de la vía de descripción ofrece la otra cuando el backend marca
  `suggested_mode`. 51 tests frontend en verde, +13, todas las llamadas mockeadas.
  Verificado además en navegador contra una base sembrada aparte, sin llamadas reales.
- **P10C (smoke aprobado) cerrada.** Cuatro situaciones, 4 llamadas de texto, cero
  imágenes y `$0.006574` frente a un techo aprobado de `$0.0482`; ningún fallback. Las
  cuatro comprobaciones objetivas pasaron en los cuatro casos y la situación vaga a
  propósito propuso en vez de pedir aclaración. **`gpt-5.4-nano` es suficiente**: no se
  sube a mini, que queda como fallback por salida inusable. El detalle medido está en
  `pricing.md` §12.
  - **Defecto corregido:** 2 de 12 propuestas prometían en el título un estampado que
    las prendas no llevaban. Como el usuario elige leyendo el título y la imagen se
    compone desde los `items`, el prompt prohíbe ahora nombrar atributos ausentes. La
    corrección no se ha revalidado contra la API; costaría una llamada (~`$0.0017`).
  - **Género no se pregunta.** Sin dato, el modelo reparte las tres propuestas entre
    registros masculinos y femeninos. Se decide **no** añadir una pregunta previa: la
    preferencia la aprenderá el archivo a partir de las elecciones reales (P11). No
    "arreglar" esto añadiendo un campo de género al compositor.

#### P11 — Archivo: portada elegible, favoritos y señal de gusto (planificada)

**Por qué ahora.** P10 dejó la app proponiendo bien, pero sin memoria: cada sesión
empieza de cero. Además, desde P10 cada elección escribe `outfits.proposal_index`, así
que la señal de gusto **ya se está acumulando** y hoy no la mira nadie. P11 cierra el
bucle: sin archivo no hay ninguna razón para volver después de ver la imagen.

**Coste: cero.** Ninguna subfase realiza una llamada al proveedor. Todo es esquema,
interfaz y datos que la aplicación ya calcula y persiste.

**Portada elegible.** `outfits.chosen_image_id`, FK nullable a `images` con
`ON DELETE SET NULL`. Cuando está a NULL la portada pasa a ser la **última**
composición, no la primera: hoy `OutfitWorkspace` usa `images[0]`, es decir, muchas
veces la que el usuario descartó a favor de una variación posterior. La última es mejor
suposición que la primera, y el campo permite corregirla explícitamente.

**Favoritos.** `outfits.is_favourite`, booleano no nulo con valor por defecto falso, y
un filtro en la biblioteca. No hace falta tabla aparte: es un atributo del outfit, no
una relación.

**Paginación real.** `GET /outfits` gana `offset`; el frontend pide páginas y ofrece
«Cargar más» mientras la última página venga llena. Se descarta la alternativa de subir
el `limit` del cliente de 24 a 100: mueve el precipicio en vez de eliminarlo, y el
recorte silencioso volvería con 101 outfits. El recuento visible es el de outfits
cargados, y la biblioteca indica cuándo quedan más por cargar en vez de callarse.

**Devolver información que ya se calcula.** `accessories_omitted` solo se pinta hoy en
`AnalysisReview`, durante el paso 2 de Crear: al abrir ese mismo outfit desde la
biblioteca no queda rastro de por qué falta una prenda en la composición. La antigüedad
de cada búsqueda guardada tampoco se muestra en el detalle. Ambos datos viajan ya en el
contrato; es trabajo de interfaz, sin backend nuevo.

**La señal de gusto se recoge, todavía no se usa.** P11 persiste y enseña de qué
propuesta salió un outfit, pero **no** alimenta con ello el prompt de propuestas.
Construir una preferencia sobre unas pocas elecciones sería inventarse un gusto que el
usuario no ha demostrado. Cuándo y cómo usarla es una decisión posterior, con datos
reales delante.

**Mutación de metadatos.** Un único `PATCH /outfits/{id}` acepta `chosen_image_id` e
`is_favourite`, ambos opcionales. Se comprueba la propiedad antes de mutar y que la
imagen elegida pertenezca a ese outfit; una imagen ajena se rechaza en vez de aceptarse
en silencio. No se crean endpoints de acción separados porque no hay pago que aislar
detrás de un clic.

**Orden de trabajo.**

- **P11A — backend:** columnas `chosen_image_id` e `is_favourite` con su migración,
  `offset` en el listado, `PATCH /outfits/{id}` con validación de propiedad y
  pertenencia, y el contrato compartido actualizado.
- **P11B — frontend:** elegir portada desde el detalle, marcar favorito y filtrar por
  ellos, «Cargar más» en la biblioteca, y mostrar `accessories_omitted` y la antigüedad
  de cada búsqueda en el detalle.

**Fuera de alcance.** Búsqueda por texto, etiquetas, ordenación distinta de la fecha,
ranking, y cualquier uso de la señal de gusto para condicionar las propuestas.

**Estado:** P11A implementada y verificada; P11B pendiente.

- **P11A (backend) cerrada.** Migración `20260903_0010` con `images.is_chosen`, su índice
  único parcial y `outfits.is_favourite`; `offset` en el listado y `PATCH /outfits/{id}`.
  305 tests backend en verde, +19, y el frontend sigue verde porque el fixture compartido
  se actualizó a la vez. Cero llamadas reales.
  - **Desviación del plan:** la elección se guardó como `images.is_chosen` en lugar de
    `outfits.chosen_image_id`. La forma planificada cerraba un ciclo de claves foráneas
    entre `outfits` e `images`, y SQLAlchemy avisaba de que no puede ordenar ese ciclo y
    de que será un error en versiones futuras. El contrato expuesto no cambia:
    `chosen_image_id` se calcula.
- **P11B (frontend) cerrada.** Portada elegible desde el detalle, favorito en la barra
  del detalle y filtro en la biblioteca, «Cargar más», y `accessories_omitted` en el
  detalle. La portada por defecto pasa a ser la última composición cuando no hay ninguna
  elegida, en vez de la primera. 64 tests frontend en verde, +13.
  - La marca de favorito de la tarjeta **no es interactiva**: la tarjeta entera ya es un
    `<button>` y anidar otro sería HTML inválido. Marcar y desmarcar se hace desde el
    detalle, que es además donde estás mirando el outfit.
  - La antigüedad de cada búsqueda ya se mostraba desde P7; ese punto del plan venía del
    informe de puntos ciegos y estaba obsoleto.
  - **Añadido fuera del plan de P11A:** `favourites_only` en el listado. Sin él, el
    filtro habría mirado solo la página cargada, que es el mismo recorte silencioso que
    esta fase venía a eliminar.

#### Horizonte posterior (no decidido en detalle)

- **P12 — Demo y publicación:** modo demo que rechaza toda llamada pagada y sirve datos ya
  generados, documentación de entrada y licencia. Es condición previa a cualquier
  despliegue público, porque el repositorio sigue sin imponer cuotas por usuario.

### Trabajo expresamente aplazado

- Recuperación o cambio de contraseña, administración avanzada, pagos, cuotas por
  usuario y publicación pública.
- PostgreSQL, storage externo, colas, workers, polling, WebSockets o generación asíncrona.
- Router o estado global de frontend, framework de UI y abstracciones genéricas.
- Proveedores alternativos y calidades `medium/high` sin evidencia nueva.
- Búsqueda completa por lote, refresco de una caché, ranking visual, stock y precios
  normalizados hasta validar primero el flujo individual.
- Actualizar Starlette/httpx solo por el aviso futuro de `TestClient`; se resolverá al
  actualizar ese conjunto de dependencias.
- Armario propio: registrar las prendas que el usuario posee y proponer únicamente con
  ellas. Exige subida de imágenes, llamadas de visión con tarifa nueva, catálogo,
  seleccionador y un lugar en `OutfitItem` para referenciar una prenda persistida, que
  hoy no existe. Se pospone hasta que P10 demuestre que el motor de propuesta funciona:
  el armario es una restricción sobre ese motor, no un motor distinto.

Se trabaja en `main` por bloques verticales y con pruebas centradas en resultados e
invariantes de alto valor. El orden anterior es un horizonte: se reevalúa al cerrar cada
bloque y no obliga a implementar infraestructura que el uso personal todavía no necesita.
