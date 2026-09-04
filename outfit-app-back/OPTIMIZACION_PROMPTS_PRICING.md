# Optimización de Prompts y Pricing — Outfit MVP

**Fecha:** 2026-07-07 · **Restricción de partida:** mismos modelos (`gpt-5.4-nano`, `gpt-5.4-mini`, `gpt-image-2`). Las palancas disponibles son: prompt de texto, prompt de imagen, composición en código, tamaño, calidad, y política de regeneración.
**Síntoma que motiva el análisis:** con outfits de muchos elementos, el board no respeta la disposición corporal (prendas "en fila" o desordenadas en vez de ordenadas de arriba abajo como se visten). Calidad `low` validada y se mantiene como base.

**Leyenda:** [REAL] demostrable con el código/datos actuales · [HIPÓTESIS] plausible, requiere el A/B de §6 antes de adoptar · [ESTILO] preferencia.

---

## 1. Resumen ejecutivo

El problema de composición **no es un problema de `quality=low`** — tu propio experimento demostró que `low` respeta bordados, lunares y cremalleras (detalle *local*). La composición es un problema *global* de la imagen, y falla por tres causas que se acumulan, todas corregibles sin cambiar de modelo y casi sin cambiar de coste:

1. **[REAL] El layout se deja al azar.** El `image_prompt` es prosa libre escrita por nano. El system prompt pide "composición vertical tipo outfit board", pero nadie dice al modelo de imagen *qué prenda va en qué zona*. La ironía: **esa información ya existe** — nano extrae `category` (`upper`/`lower`/`footwear`/`accessory`) para cada prenda y el código la ignora al construir el prompt de imagen. El mapa espacial se puede componer determinísticamente en código a coste $0.
2. **[REAL] El lienzo lucha contra el layout.** Se pide un board *vertical* sobre un lienzo *cuadrado* (1024×1024). Con 6-8 piezas no hay altura para apilarlas en orden corporal y el modelo las esparce en horizontal — exactamente el síntoma descrito. `gpt-image-2` soporta retrato (1024×1536); usado solo en outfits complejos sube el coste medio ~+9%.
3. **[HIPÓTESIS] El prompt diluye lo importante.** 13 cláusulas negativas ("sin modelo, sin manos, sin perchas…") y pocas instrucciones espaciales positivas; prompt en español cuando los modelos de imagen adhieren mejor en inglés; sin límite de accesorios que compiten por atención con las prendas.

**Los 3 movimientos recomendados, por orden:**
1. **Composición híbrida** (M): nano describe cada prenda (frase visual en inglés por ítem), el código construye el prompt final con zonas explícitas por categoría — determinista, testeable, coste neutro. Es el cambio de mayor impacto esperado.
2. **Tamaño adaptativo** (S): retrato 1024×1536 cuando `len(items) ≥ 5`. Coste blended ~+9% (tarifa por verificar). Solo si el paso 1 no basta en el A/B.
3. **`medium` selectivo como último recurso** (S): 9× el coste en el ~20% de casos = +142% blended. Solo si 1+2 fallan en el A/B — no antes, porque invalidaría el ahorro 9× que ya validaste.

**En texto:** con los mismos modelos, las mejoras son de *calidad de salida*, no de coste (el texto es el 9% del total y la caché automática de OpenAI ya lo abarata): un ejemplo few-shot, adelgazar el schema (las `VisualConstraints` son constantes que pagas como tokens de salida en cada llamada), y prefijo estable para no romper la caché.

**Todo es medible con lo que ya tienes:** `run_experiment.py` + un set nuevo de 12 outfits complejos. El A/B completo de 4 variantes cuesta **~$0.32** (§6). Regla de oro del documento: **la imagen que no se regenera es la más barata** — cada regeneración evitada ahorra $0.006, el 100% de una imagen; mejorar la composición a la primera es a la vez la mejor palanca de calidad *y* de coste.

---

## 2. Tabla de palancas priorizada

| # | Impacto | Tipo | Palanca | Dónde | Δ Coste/outfit | Δ Calidad esperada |
|---|---------|------|---------|-------|----------------|--------------------|
| P1 | Alto | REAL | Layout determinista en código usando las `category` ya extraídas (zonas corporales explícitas) | `openai_image.py:21-28` + `text_system_prompt.py` | ≈ $0 (neutro en tokens) | Directa sobre el síntoma; consistencia entre regeneraciones |
| P2 | Alto | HIPÓTESIS | Retrato 1024×1536 adaptativo (`items ≥ 5`) — el board vertical cabe en el lienzo | `openai_image.py:53` + `pricing.py` | +~9% blended (+$0.0006) | Elimina la presión horizontal en complejos |
| P3 | Medio | HIPÓTESIS | Prompt de imagen en inglés (frases visuales por ítem generadas por nano) | system prompt + schema | ≈ $0 | Mejor adherencia típica de modelos de imagen |
| P4 | Medio | HIPÓTESIS | Consolidar negativos (13 cláusulas → bloque corto) y añadir instrucciones espaciales positivas; cap de accesorios visibles (máx. 4, escala menor) | prompt de imagen (código, tras P1) | ≈ $0 | Menos dilución de atención con muchos ítems |
| P5 | Medio | HIPÓTESIS | Few-shot: 1 ejemplo canónico (outfit complejo → JSON perfecto) en el system prompt | `text_system_prompt.py` | +$0.00008 (+400 tokens input) | Mejor calibración de `certainty` y consistencia del JSON |
| P6 | Medio | REAL | Quitar `VisualConstraints` del schema del LLM: son constantes de producto que pagas como output en cada llamada y pueden variar | `schemas.py:25-33` | −$0.00006 | Menos superficie de error; schema más corto |
| P7 | Medio | REAL | `pricing.py` no conoce más tamaños: el retrato se registraría al precio `low` de 1024² → corrompe el tracking de coste **antes** de empezar el experimento | `pricing.py:15-23` | — | Prerequisito de P2 |
| P8 | Bajo | REAL | Regenerar reutiliza el prompt idéntico → los mismos fallos de composición tienden a repetirse; añadir directiva de variación ("different arrangement, same garments") en regeneración | `outfit_service.py:112` | $0 | Regeneraciones más útiles → menos cadenas de 3 regens fallidas |
| P9 | Bajo | REAL | `quality=medium` global queda **descartado** (×9 = +$0.047/outfit); solo selectivo y solo si P1+P2 fallan | política | +142% blended si selectivo 20% | Último recurso |
| P10 | Info | REAL | La estructura actual (system prompt fijo primero, user después) ya es óptima para la caché automática de prompts de OpenAI — al editar el prompt, mantener TODO lo estático al principio y lo variable al final | `openai_text.py:20-23` | ya capturado | No romperla al aplicar P5 |

---

## 3. Diagnóstico: por qué falla la composición con muchos elementos

Cadena actual de decisiones de layout:

```
system prompt: "el image_prompt debe pedir composición vertical…"   (deseo, no instrucción espacial)
      → nano escribe prosa libre en español                          (sin mapa de zonas)
      → template fallback: "composición limpia y equilibrada"        (aún más vago)
      → gpt-image-2 en lienzo 1024×1024                              (cuadrado vs. vertical)
      → low quality                                                  (menos pasos para resolver ambigüedad global)
```

Tres observaciones concretas sobre el código:

1. **La categoría de cada prenda muere en el JSON.** `generate_outfit_image(extraction.items, extraction.image_prompt)` recibe los items, pero `_build_prompt` (`openai_image.py:21-28`) solo los usa en el fallback y solo como lista plana `"color tipo, color tipo…"`. Ni el prompt de nano ni el fallback dicen nunca "la chaqueta arriba, el pantalón en medio, las botas abajo". Con 2-3 piezas el modelo lo infiere solo (por eso tus casos `simple`/`media` salen bien); con 6-8 piezas la inferencia colapsa.
2. **El template fallback ni siquiera pide orden corporal** — pide "composición limpia y equilibrada con prendas por separado", que es literalmente una descripción de "elementos en fila".
3. **En tu benchmark, la categoría `complejo` (5-6 piezas, casos 015-018) es exactamente donde está el síntoma** — y son 4 de 24 casos. El set de test para el A/B debe sobre-muestrear ese rango (§6).

Por qué NO es (probablemente) la calidad: `low` reduce el presupuesto de cómputo por imagen, lo que degrada antes la *coherencia global* que el detalle local — así que subir a `medium` sí ayudaría algo. Pero es la palanca más cara (×9) para un problema que las palancas gratis (P1-P4) atacan de frente. Orden correcto: arreglar la instrucción antes que pagar más cómputo por interpretar una instrucción vaga.

---

## 4. Rediseño del prompt de imagen (P1 + P3 + P4)

### 4.1 Principio: el LLM pone el lenguaje, el código pone la estructura

Hoy nano hace dos trabajos en el `image_prompt`: describir las prendas (lo hace bien, es un LLM) y decidir la composición (lo hace mal, porque nadie se lo especifica y no es determinista). La separación correcta:

- **nano** → una frase visual corta **en inglés** por prenda: `"black bomber jacket with small white floral embroidery on the sleeves"`.
- **código** → ensambla el prompt final: zonas corporales por `category`, estilo fotográfico, restricciones. Idéntico siempre, testeable con unit tests, y con las prendas en el orden en que se visten.

### 4.2 Cambio de schema (neutro en tokens)

```python
# schemas.py — en OutfitItem, añadir:
visual_phrase_en: str = Field(
    description="Short English visual phrase for image generation, e.g. 'black bomber jacket with white floral embroidery on the sleeves'"
)
# y ELIMINAR: VisualConstraints del output del LLM (P6) y el campo image_prompt libre.
```

Economía de tokens del cambio (nano, output a $1.25/M): +~10 tokens/ítem por `visual_phrase_en` (~+60), −~50 por `VisualConstraints`, −~120 por el `image_prompt` libre → **neto ≈ −110 tokens de salida ≈ −$0.00014/outfit**. Es decir: el rediseño completo es gratis o ligeramente más barato.

### 4.3 El constructor en código

```python
# app/prompts/image_prompt_builder.py
STYLE_BLOCK = (
    "Vertical flat-lay outfit board, e-commerce catalog style, on a plain white "
    "or very light gray background. Realistic studio product photography, uniform "
    "soft lighting, very soft shadows."
)
CONSTRAINTS_BLOCK = (
    "Every piece complete, laid flat, front-facing, fully visible, not overlapping. "
    "No person, no body parts, no mannequin, no hangers, no text, no props, "
    "no logos unless explicitly described."
)
MAX_ACCESSORIES_SHOWN = 4  # P4: cap de atención

def build_image_prompt(items: list[OutfitItem]) -> str:
    zones = {"top": [], "middle": [], "bottom": [], "side": []}
    for i in items:
        phrase = i.visual_phrase_en
        if i.category in ("upper", "one_piece"):
            zones["top"].append(phrase)
        elif i.category == "lower":
            zones["middle"].append(phrase)
        elif i.category == "footwear":
            zones["bottom"].append(phrase)
        else:
            zones["side"].append(phrase)

    dropped = len(zones["side"]) - MAX_ACCESSORIES_SHOWN
    zones["side"] = zones["side"][:MAX_ACCESSORIES_SHOWN]  # loguear si dropped > 0

    lines = [STYLE_BLOCK, (
        "Arrange the garments top-to-bottom in the order they are worn on a body, "
        "with proportions consistent as if all pieces belong to the same person:"
    )]
    if zones["top"]:    lines.append(f"- Top area: {'; '.join(zones['top'])}.")
    if zones["middle"]: lines.append(f"- Middle area, below the tops: {'; '.join(zones['middle'])}.")
    if zones["bottom"]: lines.append(f"- Bottom area: {'; '.join(zones['bottom'])}.")
    if zones["side"]:   lines.append(
        f"- Narrow right-hand column, smaller scale: {'; '.join(zones['side'])}.")
    lines.append(CONSTRAINTS_BLOCK)
    return "\n".join(lines)
```

Qué resuelve, punto por punto, respecto al síntoma:
- **"en fila como un cuerpo"** → `"top-to-bottom in the order they are worn on a body"` + zonas explícitas: la instrucción espacial deja de ser inferida.
- **Muchos elementos** → los accesorios (los que rompen la fila) van a una columna lateral a escala menor, con cap.
- **Proporciones** → `"as if all pieces belong to the same person"` ancla la escala relativa (chaqueta ≫ gafas).
- **Consistencia** → dos outfits con las mismas categorías producen prompts con la misma estructura; las regeneraciones parten del mismo esqueleto.

Notas de integración: el prompt resultante se guarda en `outfits.image_prompt` como hasta ahora → **la regeneración no cambia** (sigue reutilizando la columna). El template fallback `IMAGE_PROMPT_TEMPLATE` queda obsoleto: con `visual_phrase_en` por ítem, el builder cubre también el caso "nano no dio prompt" (que era su única función). Menos ramas, menos deuda.

### 4.4 Variante intermedia (si no quieres tocar el schema aún)

Para el A/B de §6 se puede probar primero sin cambiar `schemas.py`: reescribir en el system prompt la sección "IMAGEN FINAL DESEADA" exigiendo a nano un **esqueleto fijo** (en inglés, con las 4 líneas de zonas). Menos determinista que el builder — sigue dependiendo de que nano obedezca — pero aísla la variable "instrucción espacial" con un cambio de 10 minutos. Si el esqueleto vía LLM ya resuelve el 90%, el builder en código sigue mereciendo la pena por consistencia y testeo, pero deja de ser urgente.

### 4.5 Regeneración con variación (P8)

```python
# outfit_service.py — al regenerar:
variation = "\nAlternative composition of the exact same garments; do not add or remove any piece."
image_details = generate_outfit_image([], outfit.image_prompt + variation)
```

Coste $0. Sin esto, regenerar con prompt idéntico re-muestrea la misma distribución: si la composición falló, tiende a fallar parecido, y el usuario quema sus 3 regeneraciones en variantes del mismo error.

---

## 5. Optimización del prompt de texto (mismos modelos)

El coste de texto es el ~9% del total y la caché automática lo reduce solo; **ninguna edición del system prompt se justifica por coste** (recortarlo a la mitad ahorra ~$0.0001/outfit). Se justifican por calidad:

1. **Few-shot (P5).** El system prompt incluye un ejemplo complejo de 6 piezas con frases
   `visual_phrase_en` bien formadas. Tras la simplificación de contrato del 2026-07-18,
   el ejemplo ya no devuelve campos sin consumidor ni permite atributos inferidos.
2. **Adelgazar el schema (P6).** `VisualConstraints` son 8 booleanos/literales que siempre valen lo mismo: hoy son tokens de salida pagados en cada llamada y una oportunidad de que el modelo devuelva `human_model: true` por error. Constantes de producto → al código.
3. **No romper la caché (P10).** Al aplicar 1 y 2, mantener el orden: [reglas + ejemplo few-shot] fijos primero, mensaje de usuario al final. Cualquier contenido variable inyectado en el system prompt (fecha, idioma del usuario…) rompería el prefijo cacheable — no hacerlo.
4. **Lo que NO tocar:** `temperature=0.2` (correcto para extracción), structured outputs (correcto), la decisión de fallback en código (correcta — y con el fix F6 de la auditoría anterior aplicado, el fallback deja de dispararse en `needs_clarification`).

---

## 6. Plan experimental: medir antes de adoptar

Tu benchmark de 24 casos solo tiene 4 outfits de ≥5 piezas — insuficiente para el síntoma. Set nuevo: **12 descripciones de 5-8 piezas** (capas + accesorios variados; reutilizar las 4 `complejo` existentes + 8 nuevas), corridas con `run_experiment.py` (añadirle un parámetro de variante: ~20 líneas).

### Matriz de variantes

| Variante | Prompt | Tamaño | Calidad | Coste (12 imgs) |
|---|---|---|---|---|
| V0 | actual (baseline) | 1024×1024 | low | $0.072 |
| V1 | esqueleto vía LLM (§4.4) | 1024×1024 | low | $0.072 |
| V2 | builder en código (§4.3) | 1024×1024 | low | $0.072 |
| V3 | builder en código | 1024×1536 | low | ~$0.108* |
| V4 (solo si V2 y V3 fallan) | builder en código | 1024×1024 | medium | $0.636 |

**Total V0–V3: ~$0.32.** (*) Tarifa de retrato **por verificar contra el pricing oficial** — supuesto: coste ∝ área ⇒ ~1.5× ≈ $0.009. **Antes de correr V3, extender `pricing.py`** (P7): hoy cualquier tamaño ≠1024² se registra al precio de `low` 1024² y el tracking mentiría justo durante el experimento.

### Rúbrica de evaluación (manual, columna por imagen en el CSV)

| Criterio | 0/1 |
|---|---|
| Orden corporal correcto (arriba→abajo como se viste) | |
| Sin solapamientos / prendas completas | |
| Proporciones coherentes entre piezas | |
| Accesorios presentes pero no dominantes | |
| Detalle fino respetado (control: que el fix no lo degrade) | |

### Regla de decisión (fijada antes de mirar resultados)

- Adoptar la variante **más barata** con ≥90% en "orden corporal" sobre el set complejo.
- V3 (retrato) solo si V2 < 90% y V3 ≥ 90% → y entonces **adaptativo** (`items ≥ 5`), no global.
- V4 (medium) solo si V2 y V3 < 90% → selectivo, nunca global.
- En todo caso, V2 sustituye a V0 aunque empaten en composición, por determinismo y testabilidad (el builder tiene unit tests; la prosa de nano no).

---

## 7. Impacto en pricing de cada escenario

Base: $0.0066/outfit ($0.0006 texto + $0.006 imagen). Por 1.000 outfits, asumiendo 20% de outfits "complejos" (`items ≥ 5`) y tarifa retrato ~$0.009 (por verificar):

| Escenario | Coste/1k outfits | Δ vs. hoy | Comentario |
|---|---|---|---|
| Hoy (V0) | $6.62 | — | con el síntoma |
| V2: builder en código | $6.56 | **−1%** | neto de tokens ligeramente favorable (§4.2) |
| V2 + retrato adaptativo 20% (V3) | $7.16 | **+9%** | +$0.0006/outfit medio |
| Retrato global | $9.62 | +45% | innecesario: los simples ya salen bien en cuadrado |
| Medium selectivo 20% | $16.02 | +142% | último recurso |
| Medium global | $53.62 | +710% | descartado — desharía tu validación del 9× |

**La cuenta que lo une todo (composición ↔ regeneraciones):** cada regeneración cuesta $0.006 — lo mismo que la imagen original. Si hoy el fallo de composición provoca ~1 regeneración en el 20% de outfits complejos, eso son **+$0.0012/outfit de media**: el doble de lo que cuesta el retrato adaptativo (+$0.0006) y infinitamente más que el builder ($0). Es decir: si P1/P2 reducen esa tasa de regeneración aunque sea a la mitad, **el fix de calidad se paga solo y sobra**. Para poder hacer esta cuenta con datos reales y no con supuestos, hace falta medir la tasa de regeneración por nº de items — otra razón para el tracking de uso (F12 de la auditoría anterior).

---

## 8. Quick wins y secuencia recomendada

**Quick wins (<30 min):**
1. Variante intermedia §4.4 (esqueleto espacial vía system prompt, en inglés) — ataca el síntoma hoy mismo, $0.
2. Directiva de variación en regeneración (P8) — 2 líneas, $0.
3. Extender `pricing.py` con 1024×1536 y `ValueError` en tamaños desconocidos (P7) — prerequisito de cualquier experimento de tamaño.
4. Añadir columna de nº de items al CSV del experimento (ya está: `n_items`) y a la tabla `outfits` — habilita la cuenta regeneraciones-por-complejidad de §7.

**Secuencia (total ~1 día de trabajo + ~$0.32 de API):**
1. Quick wins 1-3 → correr V0/V1 (24 imágenes, $0.14) → confirmar que la instrucción espacial mueve la aguja.
2. Builder en código + `visual_phrase_en` + few-shot + quitar `VisualConstraints` (P1, P3, P5, P6) → correr V2 ($0.07).
3. Solo si V2 no llega al 90%: correr V3 retrato ($0.11) y decidir adaptativo.
4. Actualizar `pricing.md` §6 con la tanda 2 (mismo formato que la tanda 1) y fijar la variante ganadora.

**Preguntas abiertas:**
1. ¿El 20% de outfits complejos es realista para tus usuarios objetivo? (Si es 40%, el retrato adaptativo cuesta +18%, sigue siendo barato.)
2. ~~¿Los accesorios recortados por el cap (P4) deben avisarse al usuario o recortarse en silencio?~~ **Decidido: se avisan** (`accessories_omitted` en la respuesta).
3. ¿`visual_phrase_en` en inglés afecta a algún plan de mostrar el desglose textual al usuario en español? (No debería: `item_type`/`color`/`details` siguen en español; la frase inglesa es solo para la imagen.)

---

## 9. Estado de implementación — actualizado 2026-09-01

| Palanca | Estado |
|---|---|
| P1 builder determinista adaptativo | ✅ Implementado y validado: vertical con 1-3 piezas; composición ancha y rail semántico desde 4. Desde 2026-09-01 la geometría escala con el número de piezas y accesorios (validado con mocks) |
| P3 frases visuales en inglés (`visual_phrase_en` por item) | ✅ Implementado (schema + system prompt) |
| P4 cap de accesorios (4) + negativos consolidados | ✅ Implementado — el recorte se informa en `accessories_omitted`, no es silencioso |
| P5 few-shot (caso gabardina, 6 piezas) | ✅ Implementado en el system prompt |
| P6 `VisualConstraints` fuera del schema del LLM | ✅ Implementado (constantes en el builder) |
| P7 `pricing.py` sin infravaloración silenciosa | ✅ Implementado — `ValueError` en combinaciones no verificadas, validado **antes** de llamar a la API (nunca tras pagar) |
| P8 directiva de variación en regeneración | ✅ Implementado |
| Contrato de extracción reducido (2026-07-18) | ✅ `status` limitado a `ok/needs_clarification`; eliminados campos sin consumidor y `source`; `visual_phrase_en` obligatoria; la descripción original la aporta la aplicación |
| Relaciones explícitas (`styling_notes_en`, 2026-07-19) | ✅ Implementado en schema, builder y revisión; permite solo colocaciones expresadas por el usuario y mantiene la separación estricta en el resto |
| Calzado desparejado como un único par | ✅ Regla explícita de extracción y frase visual inequívoca; cubierto por contrato y tests, pendiente de validación visual real |
| P2 retrato 1024×1536 adaptativo | ❌ No justificado por el A/B; no implementar ahora |
| P9 `medium` selectivo | ❌ No justificado por el A/B; mantener `low` |
| Variante intermedia §4.4 | Obsoleta — se fue directamente al builder (§4.3) |

---

## 10. Investigación de vista vestida — validada el 2026-07-20

### Hipótesis mínima

La imagen principal sigue siendo el flat-lay cuadrado validado. La nueva hipótesis es si
una **segunda imagen opcional** sobre un maniquí neutro ayuda a entender cómo cae el outfit.
No se mezcla board y cuerpo en una misma salida: eso reduciría el tamaño útil de ambos,
haría más difícil regenerarlos por separado y cobraría siempre una función que el usuario
puede no querer.

Se comparan solo dos caminos reales con `gpt-image-2`:

1. `text_only_generation`: `images.generate` recibe la extracción estructurada guardada.
2. `flat_lay_reference_edit`: `images.edit` recibe la misma especificación y el flat-lay
   existente como referencia visual.

La segunda ruta puede conservar mejor estampado, material y forma, pero añade tokens de
imagen de entrada. La primera es más barata y simple, pero debe recrear visualmente las
prendas. El A/B existe para decidirlo con evidencia, no por intuición.

### Caso, control y coste

- Caso único: **C10**, con kimono estampado, top, palazzo, collar, bolso y sandalias.
- Se reutilizan `002_C10_extraction.json` y `002_C10_wide.png`: 0 llamadas de texto.
- Salida común: maniquí adulto neutro, sin rostro, cuerpo completo, pose frontal,
  `1024x1536`, calidad `low`.
- Máximo: 1 `generate` + 1 `edit`, con `max_retries=0`; un fallo no autoriza repetir.
- Salida verificada: `$0.010` entre ambas. Se reservan `$0.010` para texto de entrada,
  `$0.080` para hasta 10.000 tokens de la referencia y `$0.010` de contingencia:
  **presupuesto máximo estimado `$0.11`**. El límite efectivo de gasto lo aportan las dos
  llamadas sin reintentos; la tokenización exacta de la referencia solo se conoce en
  `usage` tras responder la API.

El `dry-run` quedó completado sin coste en
`experiments/output/20260719_151310_ab_worn_view_dry_run/`. Generó los prompts X/Y,
manifiesto, rúbrica y copia de la referencia, con `provider_call_attempts=0`. La carpeta
permanece local e ignorada por Git.

### Regla de decisión

Se puntúan X/Y antes de abrir `variant_map.json`. Deben conservar las seis piezas, colores
y detalles, mostrar capas y caída creíbles, mantener accesorios y calzado legibles, no
duplicar ni añadir elementos y resultar útiles para imaginar el outfit puesto.

- Si texto y referencia empatan, gana texto por menor coste y menor acoplamiento.
- Si la edición mejora de forma visible la fidelidad, se acepta su coste extra y el
  flat-lay se reutiliza como referencia.
- Si ninguna es útil, se descarta la vista vestida del MVP.

Esta fue la barrera previa a implementar endpoint, persistencia y botón de frontend, y se
cumplió antes de escribir la ruta de producto. El retrato aquí no reabre P2 para el board:
se usa porque una figura de cuerpo completo sí necesita un lienzo vertical; el flat-lay
principal permanece en `1024x1024 low`.

### Resultado real y decisión

La ejecución aprobada quedó en
`experiments/output/20260720_190229_ab_worn_view/`: 0 llamadas de texto, 2 llamadas
de imagen, 0 reintentos, 0 fallos y un coste de **$0.020207** calculado a partir del
`usage` devuelto y las tarifas estándar.

| Variante ciega | Ruta revelada | Coste completo | Latencia | Resultado |
|---|---|---:|---:|---|
| X | `flat_lay_reference_edit` | **$0.014252** | 24.64 s | 7/7 criterios objetivos y aceptada por el usuario |
| Y | `text_only_generation` | **$0.005955** | 18.93 s | 6/7; conserva categorías, pero no la identidad visual |

X mantuvo el estampado y la longitud del kimono, el corte y los detalles del top y
palazzo, y la forma del collar, bolso y sandalias. Y conservó las seis categorías, pero
reinterpretó esos detalles porque la extracción solo contenía frases genéricas. El
usuario confirmó que X resuelve correctamente la utilidad subjetiva de imaginar el
outfit puesto, por lo que la hipótesis queda aceptada.

**Decisión de producto aplicada:** únicamente la edición con referencia se ofrece como
segunda acción opcional y cobrada por separado. Cada vista puesta parte de un flat-lay
concreto mediante `images.edit`; la variante solo desde texto no se ofrece ni se genera
automáticamente junto con el board. Una única prueba no garantiza todos los casos futuros,
pero sí decidió la arquitectura mínima; los casos límite se corregirán solo si aparecen
en el uso real.

### Smoke de la ruta de producto aceptado

El 2026-07-22 se ejecutó por primera vez el endpoint implementado sobre una composición
persistida de tres piezas: camiseta azul marino de rayas, vaqueros azules y zapatillas
negras. No fue otro A/B ni cambió el prompt; comprobó que la decisión anterior llegaba
intacta desde FastAPI hasta persistencia y galería.

La única edición costó `$0.014137` según `usage`, tardó aproximadamente 30 segundos y
conservó piezas, colores, patrón, lavado general del vaquero y calzado. El maniquí apareció
completo, frontal y sobre fondo neutro, sin duplicados ni elementos extra. La caída de la
camiseta cambió solo de forma natural al vestirla. El usuario aceptó el resultado.

La ruta creó una única vista 1:1 y una segunda petición devolvió `created=false` sin otra
llamada de pago. Esto cierra el smoke de producto: se mantiene el prompt y la arquitectura
actuales, sin añadir variantes ni automatizar otra generación real.

Además, del registro de la auditoría: **F1 resuelto** (fallo de imagen → `ImageGenerationError`; outfit se guarda, sin fila basura, sin consumir regeneración; 502 en regeneración fallida) y **F6 resuelto** (sin fallback sobre `needs_clarification`). Suite actual: 164 tests en verde.

**Validación real completada el 2026-07-15:** el benchmark de 24 casos validaba la
arquitectura anterior; el smoke y el A/B siguientes midieron la v2.

1. ✅ Smoke real completado el 2026-07-15 con
   `venv/bin/python -m experiments.run_experiment --limit 5`: 5/5 imágenes,
   0 fallbacks, 0 errores, $0.0300 de imagen y coste completo estimado de ~$0.033.
   Revisión manual: 5/5 con piezas y atributos explícitos correctos, orden corporal,
   prendas completas sin solapamiento y ninguna persona, percha, marca, texto o
   elemento decorativo.
2. ✅ A/B ciego de composición (§6): 12 outfits de 5-8 piezas, una sola extracción
   reutilizada y 24 imágenes. Resultado completo en `pricing.md` §7: lista plana
   **83/84 (98.8%)**, builder zonado **81/84 (96.4%)**; 0 fallbacks, 0 errores y
   coste completo estimado de ~$0.1512.
3. ✅ Regresión focalizada del builder adaptativo: C08 (8 piezas) y C10 (antiguo
   solape kimono/top), reutilizando las extracciones de la tanda anterior. 2/2 imágenes,
   14/14 criterios visuales, 0 errores, $0.0120 de imagen y $0 de texto. Resultado
   completo en `pricing.md` §8.

Decisión: adoptar el builder adaptativo y no probar retrato ni `medium`. Las capas se
muestran separadas de exterior a interior, el cuerpo conserva un eje inferior y los
accesorios usan un rail semántico. El caso de medias queda resuelto de forma determinista
como legwear y cubierto por tests, sin otra generación de pago.

**Refinamiento seguro del 2026-07-19:** el test manual con una descripción deliberadamente
confusa mostró que la extracción podía reconocer una bufanda usada como cinturón, pero el
builder no tenía una vía formal para una falda colocada sobre un pantalón y el modelo visual
interpretó dos colores de botín como dos pares. Se añadió `styling_notes_en` como lista
pequeña de relaciones explícitas, sin motor genérico ni migración: forma parte del JSON ya
persistido. El builder solo relaja la separación para esas notas y el frontend las enseña
antes del gasto visual. El system prompt exige que "un botín negro y el otro marrón" sea
un único item y un único par. El cambio queda validado con mocks; no se atribuye todavía
una mejora visual real ni un coste medido.

**Corrección del 2026-09-01 — geometría proporcional al outfit:** una descripción de 4
piezas (camiseta amarilla con logo, gorra roja, pantalón cargo beige y zapatillas oscuras)
produjo una camisa de lino inventada en la banda superior y tres complementos inventados en
el rail: gafas, reloj y bandolera. La extracción fue correcta; la causa estaba en el propio
prompt compuesto. El layout ancho se afinó y validó sobre C08 y C10, de 8 y 6 piezas, pero
`WIDE_LAYOUT_MIN_ITEMS` lo dispara desde 4. Con un solo top, la banda superior pedía prendas
`side-by-side, from outermost to innermost`; con un solo accesorio, el rail reservaba un 24%
del lienzo; y la instrucción de no dejar áreas vacías no decía con qué rellenarlas. El modelo
completó el hueco de la única forma que el prompt permitía. La corrección no revierte la
decisión de julio, que sigue siendo válida para 6-8 piezas: el prompt enumera ahora las piezas
exactas del board antes del layout, la banda superior y el rail se redactan en singular cuando
contienen una sola pieza, el rail ocupa 16%, 20% o 24% según su contenido, y ambos bloques de
restricciones abren con una prohibición positiva de añadir nada no listado en lugar de confiar
en los negativos finales. Se conservan sin cambios los logotipos descritos por el usuario. El
cambio queda validado con mocks y tests de builder; no se atribuye todavía una mejora visual
real ni un coste medido.

La semántica y la concurrencia de regeneraciones también están cerradas: una primera
imagen recuperada no consume regeneración y `regeneration_leases` impide dobles llamadas
de pago, devolviendo HTTP `409` a la petición concurrente.

**Cola posterior vigente:** el entorno, CI y configuración ya están cerrados. Mientras el
uso sea personal se mantiene aplazada la capa pública; antes de exponer el servicio habrá
que añadir autenticación, ownership, cuotas, rate limiting, medición de coste por usuario
y CORS explícito. El roadmap único está en `contexto_proyecto.md`.
