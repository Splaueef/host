# meta developer: @Huai_Baike
# meta version: 1.1.0
# meta syntax: .m <приклад>
# requires: sympy pint aiohttp matplotlib numpy scipy Pillow

import asyncio
import io
import re
import urllib.parse
import aiohttp
from PIL import Image
import sympy as sp
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application,
)
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib.colors import LinearSegmentedColormap

from .. import loader, utils

MAX_RENDER_BYTES = 5 * 1024 * 1024

try:
    import pint
    ureg = pint.UnitRegistry()
    # byte, celsius, fahrenheit вже є в стандартному реєстрі pint —
    # повторне define() дає WARNING "Redefining", тому не перевизначаємо
except ImportError:
    ureg = None

try:
    from scipy import special as _scipy_special
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ═══════════════════════════════════════════════════════════════════════════════
#  АЛІАСИ — людські назви → numpy/sympy назви
# ═══════════════════════════════════════════════════════════════════════════════
GRAPH_ALIASES = {
    # Тригонометрія
    r'\btg\b':        'tan',
    r'\barcsin\b':    'asin',
    r'\barccos\b':    'acos',
    r'\barctg\b':     'atan',
    # Гіперболічні
    r'\bsh\b':        'sinh',
    r'\bch\b':        'cosh',
    r'\bth\b':        'tanh',
    r'\barcsh\b':     'arcsinh',
    r'\barcch\b':     'arccosh',
    r'\barcth\b':     'arctanh',
    r'\basinh\b':     'arcsinh',
    r'\bacosh\b':     'arccosh',
    r'\batanh\b':     'arctanh',
    # Логарифми
    r'\bln\b':        'log',
    r'\blg\b':        'log10',
    # Округлення
    r'\babs\b':       'Abs',
    r'\bsgn\b':       'sign',
    r'\bfloor\b':     'floor',
    r'\bceil\b':      'ceiling',
    r'\bnint\b':      'round',
    r'\bround\b':     'round',
    r'\bmod\b':       'Mod',
    # Комбінаторика
    r'\bfact\b':      'factorial',
    r'\bnchoosek\b':  'binomial',
    # Спеціальні функції
    r'\bgamma\b':     'gamma',
    r'\berf\b':       'erf',
    r'\berfc\b':      'erfc',
    r'\bheaviside\b': 'Heaviside',
    r'\bdirac\b':     'DiracDelta',
    # Константи-аліаси
    r'\btau\b':       '(2*pi)',
    r'\bphi\b':       '((1+sqrt(5))/2)',
    r'\bgolden\b':    '((1+sqrt(5))/2)',
}


# ═══════════════════════════════════════════════════════════════════════════════
#  numpy-реалізації спецфункцій
# ═══════════════════════════════════════════════════════════════════════════════
def _frac_np(x):      return x - np.floor(x)
def _cbrt_np(x):      return np.cbrt(x)
def _sinc_np(x):      return np.sinc(x / np.pi)  # normalized → unnormalized

def _gamma_np(x):
    return _scipy_special.gamma(x) if _HAS_SCIPY else np.vectorize(
        lambda v: float(sp.gamma(v)))(x)

def _erf_np(x):
    return _scipy_special.erf(x) if _HAS_SCIPY else np.vectorize(
        lambda v: float(sp.erf(v)))(x)

def _erfc_np(x):
    return _scipy_special.erfc(x) if _HAS_SCIPY else 1 - _erf_np(x)

def _j0_np(x):
    return _scipy_special.j0(x) if _HAS_SCIPY else np.cos(x)

def _j1_np(x):
    return _scipy_special.j1(x) if _HAS_SCIPY else np.sin(x)

def _beta_np(a, b):
    return _scipy_special.beta(a, b) if _HAS_SCIPY else np.vectorize(
        lambda a_, b_: float(sp.beta(a_, b_)))(a, b)

def _zeta_np(x):
    return np.vectorize(lambda v: float(sp.zeta(v)) if v != 1 else np.inf)(x)

def _heaviside_np(x):  return np.heaviside(x, 0.5)
def _dirac_np(x):
    eps = 0.05
    return np.exp(-0.5*(x/eps)**2) / (eps*np.sqrt(2*np.pi))

def _cot_np(x):   return np.cos(x) / np.sin(x)
def _sec_np(x):   return 1.0 / np.cos(x)
def _csc_np(x):   return 1.0 / np.sin(x)
def _coth_np(x):  return np.cosh(x) / np.sinh(x)
def _sech_np(x):  return 1.0 / np.cosh(x)
def _csch_np(x):  return 1.0 / np.sinh(x)

NP_MODULES = [
    {
        # базова тригонометрія
        'sin':      np.sin,    'cos':     np.cos,    'tan':    np.tan,
        'asin':     np.arcsin, 'acos':    np.arccos, 'atan':   np.arctan,
        'atan2':    np.arctan2,
        # похідні тригонометричні
        'cot':      _cot_np,   'sec':     _sec_np,   'csc':    _csc_np,
        # гіперболічні
        'sinh':     np.sinh,   'cosh':    np.cosh,   'tanh':   np.tanh,
        'arcsinh':  np.arcsinh,'arccosh': np.arccosh,'arctanh':np.arctanh,
        'coth':     _coth_np,  'sech':    _sech_np,  'csch':   _csch_np,
        # логарифми / степінь
        'log':      np.log,    'log2':    np.log2,   'log10':  np.log10,
        'exp':      np.exp,    'sqrt':    np.sqrt,   'cbrt':   _cbrt_np,
        # округлення
        'Abs':      np.abs,    'sign':    np.sign,
        'floor':    np.floor,  'ceiling': np.ceil,   'round':  np.round,
        'frac':     _frac_np,  'Mod':     np.mod,
        # комбінаторика
        'factorial': np.vectorize(lambda n: float(sp.factorial(int(abs(n))))),
        'binomial':  np.vectorize(lambda n, k: float(sp.binomial(int(n), int(k)))),
        # спеціальні функції
        'gamma':    _gamma_np, 'erf':     _erf_np,  'erfc':   _erfc_np,
        'sinc':     _sinc_np,
        'besselj0': _j0_np,    'besselj1':_j1_np,
        'beta':     _beta_np,  'zeta':    _zeta_np,
        'DiracDelta':_dirac_np,'Heaviside':_heaviside_np,
        # константи
        'pi':       np.pi,     'E':       np.e,
        'oo':       np.inf,    'zoo':     np.inf,
    },
    'numpy',
]


# ═══════════════════════════════════════════════════════════════════════════════
#  ПРЕПРОЦЕСОР
# ═══════════════════════════════════════════════════════════════════════════════
_WRAP1 = {   # func(x) → _np(x)
    'cot': 'cot', 'cotan': 'cot', 'ctg': 'cot',
    'sec': 'sec', 'csc': 'csc', 'cosec': 'csc',
    'coth': 'coth', 'cth': 'coth',
    'sech': 'sech', 'csch': 'csch',
    'cbrt': 'cbrt', 'frac': 'frac',
    'sinc': 'sinc',
    'besselj0': 'besselj0', 'besselj1': 'besselj1',
    'zeta': 'zeta',
}

def _preprocess(expr_str: str) -> str:
    s = expr_str.strip()
    s = s.replace('^', '**')

    # arcctg(x) → (pi/2 - atan(x))
    s = re.sub(r'\barcctg\s*\(', '(pi/2-atan(', s)

    # функції з одним аргументом — просто перейменовуємо
    for alias, canon in _WRAP1.items():
        s = re.sub(rf'\b{alias}\b', canon, s)

    # загальні аліаси
    for pattern, repl in GRAPH_ALIASES.items():
        s = re.sub(pattern, repl, s)

    return s


# ═══════════════════════════════════════════════════════════════════════════════
#  МОДУЛЬ
# ═══════════════════════════════════════════════════════════════════════════════
@loader.tds
class MathSolverMod(loader.Module):
    """Математичний монстр: графіки, параметрика, полярні, спецфункції"""
    strings = {"name": "MathSolver"}

    async def client_ready(self, client, db):
        self.client = client
        self._render_lock = asyncio.Lock()

    def parse_math(self, expr_str: str):
        s = _preprocess(expr_str)
        if '=' in s:
            left, right = s.split('=', 1)
            s = f"({left})-({right})"
        tr = standard_transformations + (implicit_multiplication_application,)
        return parse_expr(s, transformations=tr)

    def _solve(self, expression: str):
        expr = self.parse_math(expression)
        symbols = list(expr.free_symbols)
        if symbols:
            return "Корені", sp.solve(expr, symbols[0])
        return "Результат", expr.evalf()

    # ── .m ────────────────────────────────────────────────────────────────────
    async def mcmd(self, message):
        """<вираз> — Обчислити / знайти корені"""
        args = utils.get_args_raw(message)
        if not args or args.lower() == "help":
            await message.edit(
                "<b>🧮 MathSolver — команди:</b>\n\n"
                "• <code>.m 2x^2+3x-5</code> — спростити\n"
                "• <code>.m x^2-4=0</code> — корені\n"
                "• <code>.conv 50 C в F</code> — конвертер\n"
                "• <code>.mdraw sqrt(x^2+y^2)</code> — формула картинкою\n"
                "• <code>.graph sin(x), cos(x)</code> — стандартний графік\n"
                "• <code>.graph polar: r=cos(3*t)</code> — полярний\n"
                "• <code>.graph param: x=cos(t), y=sin(t)</code> — параметричний\n"
                "• <code>.graph heat: sin(x)*cos(y)</code> — heatmap\n"
                "• <code>.graphhelp</code> — всі підтримувані функції"
            )
            return
        try:
            result_label, result_value = await asyncio.to_thread(self._solve, args)
            await message.edit(
                f"<b>📝 Ввід:</b> <code>{utils.escape_html(args)}</code>\n"
                f"<b>✅ {result_label}:</b> <code>{utils.escape_html(str(result_value))}</code>"
            )
        except Exception as e:
            await message.edit(
                f"<b>❌ Помилка:</b> <code>{utils.escape_html(str(e))}</code>"
            )

    # ── .conv ─────────────────────────────────────────────────────────────────
    async def convcmd(self, message):
        """<що> в <що> — Конвертер одиниць"""
        args = utils.get_args_raw(message)
        args = re.sub(r'\s+(в|in|to)\s+', ' to ', args, flags=re.IGNORECASE)
        if " to " not in args or not ureg:
            await message.edit("<b>Приклад:</b> <code>.conv 100 km в miles</code>")
            return
        try:
            parts = args.split(" to ")
            v_str = parts[0].strip().replace(" С", " degC").replace(" Ф", " degF")
            t_str = parts[1].strip().replace("С", "degC").replace("Ф", "degF")
            val = ureg(v_str)
            res = val.to(t_str)
            await message.edit(
                f"<b>🔄 {utils.escape_html(f'{val:~P}')}</b> ⮕ "
                f"<b><code>{utils.escape_html(f'{res:.4g~P}')}</code></b>"
            )
        except Exception as e:
            await message.edit(
                f"<b>❌ Невідомо:</b> <code>{utils.escape_html(str(e))}</code>"
            )

    # ── .mdraw ────────────────────────────────────────────────────────────────
    async def mdrawcmd(self, message):
        """<формула> — Намалювати формулу як картинку (LaTeX)"""
        args = utils.get_args_raw(message)
        if not args:
            return
        await message.edit("<b>🎨 Малюю...</b>")
        try:
            latex_str = sp.latex(self.parse_math(args))
            url = (
                "https://latex.codecogs.com/png.image?"
                f"\\dpi{{300}}\\bg_white\\huge {urllib.parse.quote(latex_str)}"
            )
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as sess, sess.get(url) as r:
                if r.status != 200:
                    raise RuntimeError(f"Codecogs HTTP {r.status}")
                data = bytearray()
                async for chunk in r.content.iter_chunked(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_RENDER_BYTES:
                        raise RuntimeError("rendered image is too large")
                img = io.BytesIO(bytes(data))
                img.name = "f.png"
                await self.client.send_file(
                    message.chat_id, img, reply_to=message.reply_to_msg_id
                )
                await message.delete()
        except Exception:
            await message.edit("<b>❌ Помилка рендеру</b>")

    # ── .graphhelp ────────────────────────────────────────────────────────────
    async def graphhelpcmd(self, message):
        """Повна довідка по функціях та режимах .graph"""
        text = (
            "<b>📈 Функції для .graph — повний список:</b>\n\n"

            "<b>🔵 Тригонометрія:</b>\n"
            "  <code>sin cos tan/tg</code>\n"
            "  <code>cot/ctg/cotan</code> — котангенс\n"
            "  <code>sec csc/cosec</code> — секанс, косеканс\n"
            "  <code>asin/arcsin  acos/arccos  atan/arctg  arcctg</code>\n\n"

            "<b>🟣 Гіперболічні:</b>\n"
            "  <code>sinh/sh  cosh/ch  tanh/th</code>\n"
            "  <code>coth/cth  sech  csch</code>\n"
            "  <code>arcsinh/asinh  arccosh/acosh  arctanh/atanh  arcch  arcsh  arcth</code>\n\n"

            "<b>🟡 Степінь / корені:</b>\n"
            "  <code>sqrt(x)</code> — √x\n"
            "  <code>cbrt(x)</code> — ∛x\n"
            "  <code>x^n  x^(p/q)</code> — довільний степінь\n\n"

            "<b>🟢 Логарифми / exp:</b>\n"
            "  <code>exp(x)</code> — eˣ\n"
            "  <code>log(x)/ln(x)</code> — натуральний\n"
            "  <code>log10(x)/lg(x)</code> — десятковий\n"
            "  <code>log2(x)</code> — двійковий\n\n"

            "<b>🔶 Округлення / дробова частина:</b>\n"
            "  <code>floor(x)  ceil(x)  round(x)/nint(x)  frac(x)</code>\n\n"

            "<b>⚪ Модуль / знак / модуло:</b>\n"
            "  <code>abs(x)/Abs(x)  sign(x)/sgn(x)  mod(a,b)/Mod(a,b)</code>\n\n"

            "<b>🔢 Комбінаторика:</b>\n"
            "  <code>fact(n)</code> — n!\n"
            "  <code>nchoosek(n,k)</code> — C(n,k)\n\n"

            "<b>✨ Спеціальні функції:</b>\n"
            "  <code>gamma(x)</code> — Γ(x), гамма-функція\n"
            "  <code>erf(x)  erfc(x)</code> — функція помилок\n"
            "  <code>sinc(x)</code> — sin(x)/x\n"
            "  <code>besselj0(x)  besselj1(x)</code> — функції Бесселя J₀, J₁\n"
            "  <code>zeta(x)</code> — дзета-функція Рімана\n"
            "  <code>Heaviside(x)</code> — функція Хевісайда\n"
            "  <code>DiracDelta(x)</code> — дельта Дірака (апрокс.)\n\n"

            "<b>🔣 Константи:</b>\n"
            "  <code>pi</code> π  <code>E</code> e  <code>tau</code> 2π\n"
            "  <code>phi/golden</code> φ=(1+√5)/2\n\n"

            "<b>🗂 Режими графіка:</b>\n"
            "  Стандартний:       <code>.graph sin(x), erf(x)</code>\n"
            "  Неявна крива:      <code>.graph x^2+y^2=25</code>\n"
            "  x = f(y):          <code>.graph y^2-3</code>\n"
            "  🔵 Параметричний:  <code>.graph param: x=cos(t), y=sin(t)</code>\n"
            "     Кілька кривих:  <code>param: x=cos(t),y=sin(t); x=2cos(t),y=sin(t)</code>\n"
            "  🔴 Полярний:       <code>.graph polar: r=cos(3*t)</code>\n"
            "  🎨 Heatmap:        <code>.graph heat: sin(x)*cos(y)</code>\n\n"

            "<b>⚙️ Опції:</b>\n"
            "  Діапазон: <code>.graph gamma(x) [-4, 4]</code>\n"
            "  Кілька:   <code>.graph sin(x), cos(x), tan(x)</code>\n\n"

            "<b>💡 Приклади:</b>\n"
            "  <code>.graph polar: r=1+cos(t)</code> — кардіоїда\n"
            "  <code>.graph polar: r=cos(5*t)</code> — троянда\n"
            "  <code>.graph param: x=t*cos(t), y=t*sin(t)</code> — спіраль\n"
            "  <code>.graph heat: sin(sqrt(x^2+y^2))</code> — хвилі\n"
            "  <code>.graph heat: erf(x)*cos(y)</code>\n"
            "  <code>.graph gamma(x), erf(x) [-4,4]</code>\n"
            "  <code>.graph besselj0(x), besselj1(x) [0,20]</code>\n"
            "  <code>.graph zeta(x) [2,10]</code>"
        )
        await message.edit(text)

    # ── .graph ────────────────────────────────────────────────────────────────
    async def graphcmd(self, message):
        """<формула> [діапазон] | param: | polar: | heat: — Графік"""
        async with self._render_lock:
            await self._graph(message)

    async def _graph(self, message):
        args = utils.get_args_raw(message)
        if not args:
            await message.edit(
                "<b>Використання:</b> <code>.graph sin(x)</code>\n"
                "<code>.graphhelp</code> — всі функції та режими"
            )
            return
        await message.edit("<b>📈 Генерую графік...</b>")

        fig = None
        try:
            # ── Визначення режиму ─────────────────────────────────────────────
            mode = 'standard'
            if re.match(r'(?i)param\s*:', args):
                mode = 'parametric'
                args = re.sub(r'(?i)^param\s*:\s*', '', args)
            elif re.match(r'(?i)polar\s*:', args):
                mode = 'polar'
                args = re.sub(r'(?i)^polar\s*:\s*', '', args)
            elif re.match(r'(?i)heat\s*:', args):
                mode = 'heatmap'
                args = re.sub(r'(?i)^heat\s*:\s*', '', args)

            # ── Розбір діапазону ──────────────────────────────────────────────
            x_min, x_max = -10.0, 10.0
            range_match = re.search(
                r'\[\s*(-?[\d.]*\*?pi|[-\d.]+)\s*,\s*(-?[\d.]*\*?pi|[-\d.]+)\s*\]',
                args, re.IGNORECASE
            )
            if range_match:
                lo = float(sp.sympify(_preprocess(range_match.group(1))))
                hi = float(sp.sympify(_preprocess(range_match.group(2))))
                x_min, x_max = lo, hi
                args = args[:range_match.start()].strip().rstrip(',')

            COLORS = [
                '#FF4C4C', '#4CFF88', '#4C9FFF', '#FF4CFF',
                '#FFD700', '#FF8C00', '#00CED1', '#ADFF2F',
                '#FF69B4', '#7FFFD4', '#FFA07A', '#9370DB',
            ]

            if mode == 'parametric':
                await self._graph_parametric(message, args, x_min, x_max, COLORS)
                return
            elif mode == 'polar':
                await self._graph_polar(message, args, COLORS)
                return
            elif mode == 'heatmap':
                await self._graph_heatmap(message, args, x_min, x_max)
                return

            # ── Стандартний режим ─────────────────────────────────────────────
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(9, 7), dpi=130)
            fig.patch.set_facecolor('#1a1a2e')
            ax.set_facecolor('#16213e')

            N = 800
            x_vals = np.linspace(x_min, x_max, N)
            y_range = np.linspace(x_min, x_max, N)
            X, Y = np.meshgrid(x_vals, y_range)

            funcs = [f.strip() for f in args.split(',') if f.strip()]

            for i, f_str in enumerate(funcs):
                color = COLORS[i % len(COLORS)]
                try:
                    expr = self.parse_math(f_str)
                except Exception:
                    ax.text(0.02, 0.95 - i * 0.05, f"⚠ парсинг: {f_str}",
                            transform=ax.transAxes, color='red', fontsize=8)
                    continue

                sym_names = {s.name for s in expr.free_symbols}
                x_sym, y_sym = sp.Symbol('x'), sp.Symbol('y')

                if 'y' in sym_names and 'x' in sym_names:
                    f_np = sp.lambdify((x_sym, y_sym), expr, modules=NP_MODULES)
                    with np.errstate(all='ignore'):
                        Z = np.array(f_np(X, Y), dtype=float)
                    ax.contour(X, Y, Z, levels=[0], colors=[color], linewidths=2)
                    ax.plot([], [], color=color, linewidth=2, label=f_str)

                elif 'y' in sym_names:
                    f_np = sp.lambdify(y_sym, expr, modules=NP_MODULES)
                    with np.errstate(all='ignore'):
                        x_out = np.array(f_np(y_range), dtype=float)
                    x_out = np.where(np.abs(x_out) > 1e6, np.nan, x_out)
                    ax.plot(x_out, y_range, color=color, linewidth=2,
                            label=f"x={f_str}")

                else:
                    f_np = sp.lambdify(x_sym, expr, modules=NP_MODULES)
                    with np.errstate(all='ignore'):
                        y_out = np.array(f_np(x_vals), dtype=float)
                    dy = np.diff(y_out)
                    thresh = 5 * np.nanmedian(np.abs(dy)) + 1
                    y_out[:-1][np.abs(dy) > thresh] = np.nan
                    y_out = np.where(np.abs(y_out) > 1e6, np.nan, y_out)
                    ax.plot(x_vals, y_out, color=color, linewidth=2,
                            label=f"y={f_str}")

            self._style_ax(ax, funcs)
            await self._send_fig(message, fig, ', '.join(funcs))

        except Exception as e:
            if fig is not None:
                plt.close(fig)
            await message.edit(
                f"<b>❌ Помилка:</b> <code>{utils.escape_html(str(e))}</code>"
            )

    # ── Параметричний ─────────────────────────────────────────────────────────
    async def _graph_parametric(self, message, args, t_min, t_max, COLORS):
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(9, 7), dpi=130)
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#16213e')

        t_sym = sp.Symbol('t')
        T = np.linspace(t_min, t_max, 1500)
        labels = []

        for i, curve in enumerate(re.split(r';\s*', args)):
            color = COLORS[i % len(COLORS)]
            m = re.search(r'x\s*=\s*([^,]+),\s*y\s*=\s*(.+)', curve, re.IGNORECASE)
            if not m:
                continue
            xs, ys = m.group(1).strip(), m.group(2).strip()
            try:
                fx = sp.lambdify(t_sym, self.parse_math(xs), modules=NP_MODULES)
                fy = sp.lambdify(t_sym, self.parse_math(ys), modules=NP_MODULES)
                with np.errstate(all='ignore'):
                    Xp = np.array(fx(T), dtype=float)
                    Yp = np.array(fy(T), dtype=float)
                lbl = f"x={xs}, y={ys}"
                ax.plot(Xp, Yp, color=color, linewidth=2, label=lbl)
                labels.append(lbl)
            except Exception as e:
                ax.text(0.02, 0.95, f"⚠ {e}", transform=ax.transAxes,
                        color='red', fontsize=8)

        self._style_ax(ax, labels, "Параметричний")
        await self._send_fig(message, fig, "param: " + args)

    # ── Полярний ──────────────────────────────────────────────────────────────
    async def _graph_polar(self, message, args, COLORS):
        fig = plt.figure(figsize=(8, 8), dpi=130)
        fig.patch.set_facecolor('#1a1a2e')
        ax = fig.add_subplot(111, projection='polar')
        ax.set_facecolor('#16213e')
        ax.tick_params(colors='#aaaacc')
        ax.spines['polar'].set_color('#333355')

        T = np.linspace(0, 4 * np.pi, 2000)
        t_sym = sp.Symbol('t')

        for i, curve in enumerate(re.split(r';\s*', args)):
            color = COLORS[i % len(COLORS)]
            r_str = re.sub(r'(?i)^r\s*=\s*', '', curve.strip())
            try:
                fr = sp.lambdify(t_sym, self.parse_math(r_str), modules=NP_MODULES)
                with np.errstate(all='ignore'):
                    R = np.array(fr(T), dtype=float)
                ax.plot(T, np.abs(R), color=color, linewidth=2, label=f"r={r_str}")
            except Exception as e:
                fig.text(0.5, 0.02, f"⚠ {e}", color='red', ha='center')

        _h, _l = ax.get_legend_handles_labels()
        if _h:
            ax.legend(loc='upper right', fontsize=9,
                      facecolor='#1a1a2e', edgecolor='#444466', labelcolor='#ccccee')
        ax.set_title(f"Полярний: {args}", fontsize=10, color='#9999bb', pad=12)
        await self._send_fig(message, fig, "polar: " + args)

    # ── Heatmap ───────────────────────────────────────────────────────────────
    async def _graph_heatmap(self, message, args, x_min, x_max):
        fig, ax = plt.subplots(figsize=(9, 8), dpi=130)
        fig.patch.set_facecolor('#1a1a2e')

        N = 500
        xs = np.linspace(x_min, x_max, N)
        ys = np.linspace(x_min, x_max, N)
        X, Y = np.meshgrid(xs, ys)
        x_sym, y_sym = sp.Symbol('x'), sp.Symbol('y')

        try:
            expr = self.parse_math(args)
            f_np = sp.lambdify((x_sym, y_sym), expr, modules=NP_MODULES)
            with np.errstate(all='ignore'):
                Z = np.array(f_np(X, Y), dtype=float)
            cmap = LinearSegmentedColormap.from_list(
                'c', ['#0d0221', '#3d0066', '#7b00d4',
                      '#00b4d8', '#90e0ef', '#ffffff'])
            im = ax.imshow(Z, extent=[x_min, x_max, x_min, x_max],
                           origin='lower', cmap=cmap, aspect='auto')
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            ax.contour(X, Y, Z, levels=12, colors='white', alpha=0.2, linewidths=0.6)
        except Exception as e:
            ax.text(0.5, 0.5, f"Помилка: {e}",
                    transform=ax.transAxes, color='red', ha='center')

        ax.set_title(f"Heatmap: {args}", fontsize=10, color='#9999bb')
        ax.tick_params(colors='#aaaacc')
        for sp_ in ax.spines.values():
            sp_.set_color('#333355')
        await self._send_fig(message, fig, "heat: " + args)

    # ── .graphanim ────────────────────────────────────────────────────────────
    async def graphanimcmd(self, message):
        """<формула1> == <формула2> [кадри] [fps] — Анімований морфінг"""
        async with self._render_lock:
            await self._graphanim(message)

    async def _graphanim(self, message):
        """Анімований морфінг між двома heatmap-формулами (GIF)

        Підтримувані режими морфінгу:
          heat: f1 == heat: f2   — heatmap → heatmap
          f1 == f2               — y=f(x) → y=f(x)
          polar: r1 == polar: r2 — полярна → полярна
          param: ... == param: ...

        Опції (в кінці рядка):
          frames=N   — кількість кадрів (за замовч. 50, макс. 120)
          fps=N      — швидкість (за замовч. 25)
          loop       — петля туди-назад (ping-pong)

        Приклади:
          .graphanim heat: sin(x*y) == heat: cos(x^2+y^2)
          .graphanim polar: r=cos(3*t) == polar: r=sin(5*t) frames=60 loop
          .graphanim sin(x) == cos(x) frames=40 fps=20
        """
        raw = utils.get_args_raw(message)
        if not raw or '==' not in raw:
            await message.edit(
                "<b>Використання:</b>\n"
                "<code>.graphanim heat: sin(x*y) == heat: cos(x^2+y^2)</code>\n"
                "<code>.graphanim polar: r=cos(3*t) == polar: r=sin(5*t) loop</code>\n"
                "<code>.graphanim sin(x) == cos(x) frames=40 fps=15</code>"
            )
            return

        # ── Парсимо опції з кінця рядка ──────────────────────────────────────
        frames   = 50
        fps      = 25
        pingpong = False

        def _pop_opt(text, key, default):
            m = re.search(rf'\b{key}=(\d+)', text)
            if m:
                text = text[:m.start()] + text[m.end():]
                return text.strip(), int(m.group(1))
            return text, default

        raw, frames   = _pop_opt(raw, 'frames', frames)
        raw, fps      = _pop_opt(raw, 'fps',    fps)
        frames        = max(2, min(frames, 120))
        fps           = max(1, min(fps, 50))
        if re.search(r'\bloop\b', raw, re.IGNORECASE):
            pingpong = True
            raw = re.sub(r'\bloop\b', '', raw, flags=re.IGNORECASE).strip()

        # ── Розбиваємо на дві частини по == ──────────────────────────────────
        parts = raw.split('==', 1)
        expr_a_raw = parts[0].strip()
        expr_b_raw = parts[1].strip()

        # ── Визначаємо режим (з першої частини) ──────────────────────────────
        mode = 'standard'
        for tag in ('heat', 'polar', 'param'):
            if re.match(rf'(?i){tag}\s*:', expr_a_raw):
                mode = tag
                break

        def _strip_tag(s):
            return re.sub(r'(?i)^(heat|polar|param)\s*:\s*', '', s).strip()

        a_str = _strip_tag(expr_a_raw)
        b_str = _strip_tag(expr_b_raw)

        await message.edit(
            f"<b>🎬 Генерую анімацію ({frames} кадрів, {fps} fps)...</b>"
        )

        try:
            frames_imgs = await asyncio.to_thread(
                self._anim_render, mode, a_str, b_str, frames, pingpong
            )
        except Exception as e:
            await message.edit(
                f"<b>❌ Помилка рендеру:</b> <code>{utils.escape_html(str(e))}</code>"
            )
            return

        # ── Збираємо GIF через Pillow ─────────────────────────────────────────
        try:
            duration_ms = max(20, 1000 // fps)
            def _encode_gif():
                buffer = io.BytesIO()
                frames_imgs[0].save(
                    buffer,
                    format='GIF',
                    save_all=True,
                    append_images=frames_imgs[1:],
                    loop=0,
                    duration=duration_ms,
                    optimize=False,
                )
                return buffer

            gif_buf = await asyncio.to_thread(_encode_gif)
            gif_buf.seek(0)
            gif_buf.name = "morph.gif"

            caption = (
                f"<b>🎬 Морфінг:</b>\n"
                f"<code>{utils.escape_html(a_str[:60])}{'…' if len(a_str)>60 else ''}</code>\n"
                f"<b>→</b>\n"
                f"<code>{utils.escape_html(b_str[:60])}{'…' if len(b_str)>60 else ''}</code>\n"
                f"<i>{frames} кадрів · {fps} fps"
                f"{' · loop' if pingpong else ''}</i>"
            )
            await self.client.send_file(
                message.chat_id, gif_buf,
                reply_to=message.reply_to_msg_id,
                caption=caption,
            )
            await message.delete()
        except Exception as e:
            await message.edit(
                f"<b>❌ Помилка GIF:</b> <code>{utils.escape_html(str(e))}</code>"
            )
        finally:
            for frame in frames_imgs:
                frame.close()

    def _anim_render(
        self, mode: str, a_str: str, b_str: str,
        n_frames: int, pingpong: bool
    ) -> list:
        """Рендеримо список PIL.Image для кожного кадру морфінгу."""

        # Сітка (менша ніж у звичайному heatmap — для швидкості)
        N      = 220
        xy_lim = 10.0
        xs     = np.linspace(-xy_lim, xy_lim, N)
        X, Y   = np.meshgrid(xs, xs)
        t_vals = np.linspace(-xy_lim, xy_lim, 1200)
        T      = np.linspace(0, 4 * np.pi, 2000)

        x_sym = sp.Symbol('x')
        y_sym = sp.Symbol('y')
        t_sym = sp.Symbol('t')

        CMAP = LinearSegmentedColormap.from_list(
            'night', ['#03010f', '#0a0530', '#3d0066',
                      '#7b00d4', '#00b4d8', '#caf0f8', '#ffffff']
        )

        # ── Компілюємо обидві функції заздалегідь ────────────────────────────
        def _compile_heat(s):
            expr = self.parse_math(s)
            return sp.lambdify((x_sym, y_sym), expr, modules=NP_MODULES)

        def _compile_std(s):
            expr = self.parse_math(s)
            syms = {sym.name for sym in expr.free_symbols}
            if 'y' in syms and 'x' in syms:
                return ('implicit', sp.lambdify((x_sym, y_sym), expr, modules=NP_MODULES))
            elif 'y' in syms:
                return ('x_of_y', sp.lambdify(y_sym, expr, modules=NP_MODULES))
            else:
                return ('y_of_x', sp.lambdify(x_sym, expr, modules=NP_MODULES))

        def _compile_polar(s):
            r_str = re.sub(r'(?i)^r\s*=\s*', '', s)
            return sp.lambdify(t_sym, self.parse_math(r_str), modules=NP_MODULES)

        def _compile_param(s):
            m = re.search(r'x\s*=\s*([^,]+),\s*y\s*=\s*(.+)', s, re.IGNORECASE)
            if not m:
                raise ValueError(f"Не знайдено x=..,y=.. у: {s}")
            fx = sp.lambdify(t_sym, self.parse_math(m.group(1).strip()), modules=NP_MODULES)
            fy = sp.lambdify(t_sym, self.parse_math(m.group(2).strip()), modules=NP_MODULES)
            return fx, fy

        # ── Обчислюємо масиви A і B (базові стани) ───────────────────────────
        def _eval_heat(fn):
            with np.errstate(all='ignore'):
                Z = np.array(fn(X, Y), dtype=float)
            Z = np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0)
            return Z

        def _norm(Z):
            lo, hi = np.nanpercentile(Z, 2), np.nanpercentile(Z, 98)
            if hi - lo < 1e-12:
                return np.zeros_like(Z)
            return np.clip((Z - lo) / (hi - lo), 0, 1)

        # ── Перетворення Z → PIL кадр ─────────────────────────────────────────
        def _z_to_pil(Z_norm):
            rgba = (CMAP(Z_norm) * 255).astype(np.uint8)
            return Image.fromarray(rgba, 'RGBA').convert('RGB').resize(
                (480, 480), Image.LANCZOS
            )

        # ── Рендер кадрів ─────────────────────────────────────────────────────
        pil_frames = []
        alphas = np.linspace(0, 1, n_frames)
        if pingpong:
            alphas = np.concatenate([alphas, alphas[::-1]])

        if mode == 'heat':
            fa = _compile_heat(a_str)
            fb = _compile_heat(b_str)
            Za = _norm(_eval_heat(fa))
            Zb = _norm(_eval_heat(fb))
            for α in alphas:
                Z = (1 - α) * Za + α * Zb
                pil_frames.append(_z_to_pil(Z))

        elif mode == 'standard':
            # Рендеримо обидва стани як heatmap через контур на сітці
            def _std_to_Z(kind_fn):
                kind, fn = kind_fn
                with np.errstate(all='ignore'):
                    if kind == 'implicit':
                        Z = np.array(fn(X, Y), dtype=float)
                    elif kind == 'x_of_y':
                        x_out = np.array(fn(xs), dtype=float)
                        Z = X - x_out[np.newaxis, :]
                    else:  # y_of_x
                        y_out = np.array(fn(xs), dtype=float)
                        Z = Y - y_out[:, np.newaxis]
                return np.nan_to_num(np.abs(np.sin(Z * 2)), nan=0.0)

            fa = _compile_std(a_str)
            fb = _compile_std(b_str)
            Za = _norm(_std_to_Z(fa))
            Zb = _norm(_std_to_Z(fb))
            for α in alphas:
                Z = (1 - α) * Za + α * Zb
                pil_frames.append(_z_to_pil(Z))

        elif mode == 'polar':
            fra = _compile_polar(a_str)
            frb = _compile_polar(b_str)
            with np.errstate(all='ignore'):
                Ra = np.abs(np.array(fra(T), dtype=float))
                Rb = np.abs(np.array(frb(T), dtype=float))
            Ra = np.nan_to_num(Ra)
            Rb = np.nan_to_num(Rb)

            for α in alphas:
                R = (1 - α) * Ra + α * Rb
                fig = plt.figure(figsize=(4.8, 4.8), dpi=100)
                fig.patch.set_facecolor('#03010f')
                ax = fig.add_subplot(111, projection='polar')
                ax.set_facecolor('#0a0530')
                ax.plot(T, R, color='#00b4d8', linewidth=1.2)
                ax.tick_params(colors='#334466')
                ax.spines['polar'].set_color('#222244')
                ax.set_yticks([])
                buf = io.BytesIO()
                fig.savefig(buf, format='png', bbox_inches='tight',
                            facecolor='#03010f', dpi=100)
                buf.seek(0)
                pil_frames.append(Image.open(buf).copy().convert('RGB'))
                plt.close(fig)

        elif mode == 'param':
            fxa, fya = _compile_param(a_str)
            fxb, fyb = _compile_param(b_str)
            with np.errstate(all='ignore'):
                Xa = np.nan_to_num(np.array(fxa(t_vals), dtype=float))
                Ya = np.nan_to_num(np.array(fya(t_vals), dtype=float))
                Xb = np.nan_to_num(np.array(fxb(t_vals), dtype=float))
                Yb = np.nan_to_num(np.array(fyb(t_vals), dtype=float))

            for α in alphas:
                Xf = (1 - α) * Xa + α * Xb
                Yf = (1 - α) * Ya + α * Yb
                fig, ax = plt.subplots(figsize=(4.8, 4.8), dpi=100)
                fig.patch.set_facecolor('#03010f')
                ax.set_facecolor('#0a0530')
                ax.plot(Xf, Yf, color='#00b4d8', linewidth=1.0)
                ax.axis('off')
                buf = io.BytesIO()
                fig.savefig(buf, format='png', bbox_inches='tight',
                            facecolor='#03010f', dpi=100)
                buf.seek(0)
                pil_frames.append(Image.open(buf).copy().convert('RGB'))
                plt.close(fig)

        else:
            raise ValueError(f"Невідомий режим: {mode}")

        return pil_frames

    # ── Допоміжні ─────────────────────────────────────────────────────────────
    def _style_ax(self, ax, labels=None, title_prefix=""):
        ax.axhline(0, color='#555577', linewidth=1)
        ax.axvline(0, color='#555577', linewidth=1)
        ax.grid(True, linestyle='--', alpha=0.3, color='#444466')
        ax.tick_params(colors='#aaaacc')
        for sp_ in ax.spines.values():
            sp_.set_color('#333355')
        ax.xaxis.set_major_locator(ticker.AutoLocator())
        ax.yaxis.set_major_locator(ticker.AutoLocator())
        if labels:
            _handles, _lbls = ax.get_legend_handles_labels()
            if _handles:
                ax.legend(loc='upper right', fontsize=9,
                          facecolor='#1a1a2e', edgecolor='#444466',
                          labelcolor='#ccccee')
            t = "  |  ".join(labels[:4]) + ("…" if len(labels) > 4 else "")
            if title_prefix:
                t = f"{title_prefix}: {t}"
            ax.set_title(t, fontsize=9, color='#9999bb', pad=8)

    async def _send_fig(self, message, fig, caption_text):
        buf = io.BytesIO()
        try:
            fig.savefig(buf, format='png', bbox_inches='tight',
                        facecolor=fig.get_facecolor())
        finally:
            plt.close(fig)
        buf.seek(0)
        buf.name = "graph.png"
        await self.client.send_file(
            message.chat_id, buf,
            reply_to=message.reply_to_msg_id,
            caption=f"<b>📈 Графік:</b> <code>{utils.escape_html(caption_text)}</code>"
        )
        await message.delete()
