# Outfit App Front

Frontend local en React + TypeScript para revisar y generar outfits usando el backend
FastAPI de Outfit App.

## Acceso

La primera pantalla permite iniciar sesión o crear una cuenta normal con nombre de
usuario y contraseña. La migración del backend crea `admin` / `test`: esa cuenta mantiene
la vista técnica y puede revisar todos los outfits. Una cuenta creada desde la interfaz
solo ve sus propios outfits y recibe una experiencia simplificada sin prompts, modelos,
consultas internas ni costes. Ambas vistas conservan las mismas acciones de producto.

## Vías de entrada

El compositor ofrece dos vías sobre el mismo campo de texto, con un conmutador
explícito y sin detección automática de intención:

- **`Sé lo que quiero`** es el comportamiento de siempre: describes el outfit y se
  analiza. No cambia nada de lo anterior.
- **`Inspírame`** cuenta una situación («boda de tarde en octubre, en el campo») y el
  backend devuelve tres propuestas completas. Elegir una la convierte en un análisis y
  entra en el mismo paso de revisión; elegir **no llama al proveedor** porque la
  extracción ya está guardada, y elegir una no cierra las otras dos.

Las propuestas ya pagadas se recuperan al recargar mediante `active_proposal_id` en
`localStorage`, con el mismo criterio que `active_outfit_id`: solo el identificador.
Cuando la vía de descripción rechaza un texto que en realidad era una situación, el
aviso ofrece pasar a la otra vía en lugar de un error seco.

## Flujo

1. El usuario describe el outfit en español escribiendo o, cuando el navegador lo
   permite, mediante el botón de micrófono. El dictado solo añade texto al borrador: no
   envía el formulario ni llama a OpenAI. Si el navegador no implementa reconocimiento
   de voz, el botón queda desactivado y se conserva la escritura manual.
2. El frontend pide al backend que analice el texto con `generate_image: false`.
3. Se muestran primero las prendas detectadas. En la vista administradora aparecen
   además las relaciones explícitas, el coste calculado y el prompt técnico exacto; la
   cuenta normal evita esos detalles internos.
4. Solo una confirmación explícita genera la composición (el flat-lay técnico).
5. Después del resultado, otro botón explícito viste un maniquí neutro usando ese flat-lay
   como referencia, sin repetir el análisis de texto. El admin ve antes el prompt exacto y
   el coste habitual; para una cuenta normal la confirmación sigue siendo explícita pero
   se presenta sin información técnica.
6. La cabecera separa `Crear` y `Biblioteca`. Cambiar de sección conserva la descripción,
   la revisión o el resultado activo y nunca inicia una llamada al backend.
7. La biblioteca agrupa los resultados como tarjetas visuales amplias y usa la primera
   composición como portada estable. Cada tarjeta muestra fecha, número de composiciones
   y descripción; los análisis pendientes conservan un estado reconocible sin inventar
   una imagen.
8. Al abrir una tarjeta, la cuadrícula deja paso a un detalle independiente. Desde él se
   vuelve a la misma tarjeta y se ve directamente la composición elegida junto a su vista
   puesta, cuando existe. Una composición sin vista se limita a un ancho cómodo; el
   selector de regeneraciones solo aparece si hay más de una.
9. Desde ese detalle se puede descargar cada resultado con una acción diferenciada y
   solicitar una vista puesta para una composición histórica. El prompt y el coste solo
   aparecen al admin. Una sola operación visual puede estar activa a la vez en la interfaz.
10. Todos los análisis guardados aparecen en la biblioteca, incluidos los que todavía
   no tienen composición. Al continuar uno se recupera su estado actual desde el backend.
11. El navegador conserva únicamente `active_outfit_id` en `localStorage`. Al recargar,
   reconstruye la revisión pendiente o el último resultado con su vista puesta; no guarda
   prompts, imágenes ni extracciones duplicadas en el navegador.
12. El logo y la pestaña `Crear` vuelven al trabajo activo sin borrarlo. Solo `Nuevo
    outfit` limpia explícitamente el flujo; esa acción se bloquea mientras existe una
    operación incompatible. La navegación sigue disponible y la cabecera muestra la
    actividad en curso.
13. El detalle permite eliminar el outfit tras una confirmación irreversible. Mientras
    se procesa bloquea acciones incompatibles; al terminar lo retira del archivo y, si
    era el activo, limpia `active_outfit_id` y vuelve al formulario vacío.
14. Debajo de las imágenes del detalle aparece una única sección de búsqueda. El admin
    ve la consulta base y la estimación conservadora de `≈ $0.03`; una cuenta normal ve
    solo la prenda, sus atributos y la acción. Un único campo permite añadir una marca,
    color, material o cualquier otro detalle; si la extracción es insuficiente, el campo
    pasa a ser obligatorio. Una marca ya interpretada se muestra entre los atributos y no
    hace falta repetirla. Una petición Responses puede usar una o dos acciones
    web, sin reintentos, y después muestra hasta tres enlaces de tiendas y recupera el
    resultado guardado al recargar.

Una descripción insuficiente vuelve al formulario con la aclaración del backend. El
frontend no completa detalles ni genera imágenes por su cuenta.
La revisión ya ha realizado una llamada de texto de coste bajo, aunque ese dato solo se
expone en la vista admin. Cada llamada de imagen empieza únicamente con su propia acción
explícita; un fallo de la vista puesta conserva
la composición y no provoca un reintento automático. Durante una generación se muestra
un estado estable, sin porcentaje ficticio, y cualquier fallo ofrece un reintento manual
concreto. Abrir el archivo nunca inicia una búsqueda de producto. Mientras una búsqueda
está activa se bloquean las demás operaciones pagadas y el borrado; un fallo conserva
todo lo anterior, muestra una sola explicación y solo admite reintento manual.

## Desarrollo local

Requisitos: Node.js 20.19+ y el backend ejecutándose en `http://127.0.0.1:8000`.

```bash
npm install
npm run dev
```

Vite redirige `/health`, `/auth`, `/outfits` e `/images` al backend local. Para apuntar a
otra URL, copia `.env.example` a `.env.local` y configura `VITE_API_BASE_URL`; con sesiones
por cookie, ese destino debe compartir origen o configurar explícitamente cookies y CORS.

Antes de arrancar el backend por primera vez tras cambios de esquema:

```bash
cd ../outfit-app-back
venv/bin/python -m alembic upgrade head
venv/bin/python -m uvicorn app.main:app --reload
```

Estos comandos parten de `outfit-app-front/`; ambas aplicaciones viven en el mismo
monorepo.

## Calidad

```bash
npm run check
```

Estado verificado el 2026-08-12: el comando ejecuta lint, **33 tests** con llamadas
simuladas y build de producción. Los tests del frontend nunca realizan llamadas reales
a OpenAI. La suite cubre inicio, registro, cierre de sesión, continuidad del flujo y que
una cuenta normal no muestre información técnica. P2, P3, P4A y P5.1 conservan sus
validaciones funcionales; las dos vistas por rol requieren una última revisión visual
manual antes de cerrar P8.
