# Manual Guide: Rebuild Calculus Tables Without Watermark

Goal: do not reuse the watermarked source image as the final visual. Rebuild the table as a clean vector image from mathematical structure.

## Recommended Manual Workflow

1. Use LaTeX TikZ, PowerPoint/Word shapes, Figma, or an SVG editor.
2. Draw the outer rectangle first.
3. Draw horizontal row separators.
4. Draw the left label column.
5. Draw single or double vertical lines at discontinuities/asymptotes.
6. Place math labels at fixed coordinates: `x`, `f'(x)`, `f(x)`, `-∞`, `0`, `2`, `+∞`.
7. Place signs in the derivative row.
8. Draw variation arrows manually with arrowheads.
9. Export to SVG/PNG/PDF.
10. Keep the original crop only for review, not as the published image.

## TikZ Example

```tex
\begin{tikzpicture}[>=stealth, scale=1]
  \draw (0,0) rectangle (8,2.7);
  \draw (0,2.1)--(8,2.1);
  \draw (0,1.5)--(8,1.5);
  \draw (0.8,0)--(0.8,2.7);

  \draw (3.2,0)--(3.2,2.1);
  \draw (3.28,0)--(3.28,2.1);
  \draw (5.6,0)--(5.6,2.1);
  \draw (5.68,0)--(5.68,2.1);

  \node at (0.4,2.4) {$x$};
  \node at (0.4,1.8) {$f'(x)$};
  \node at (0.4,0.75) {$f(x)$};

  \node at (1.3,2.4) {$-\infty$};
  \node at (3.24,2.4) {$0$};
  \node at (5.64,2.4) {$2$};
  \node at (7.4,2.4) {$+\infty$};

  \node at (2.0,1.8) {$-$};
  \node at (4.4,1.8) {$-$};
  \node at (6.6,1.8) {$+$};

  \node at (1.1,1.1) {$0$};
  \draw[->] (1.4,1.05)--(2.7,0.35);
  \node at (2.95,0.25) {$-\infty$};

  \node at (3.65,1.05) {$+\infty$};
  \draw[->] (4.0,0.95)--(5.1,0.35);
  \node at (5.25,0.25) {$-2$};

  \draw[->] (6.0,0.35)--(7.2,1.1);
  \node at (7.45,1.05) {$+\infty$};
\end{tikzpicture}
```

## Pipeline Direction

Use `visual_table` as structured data, then render with an SVG template. For high fidelity, store coordinates for:

- outer frame size
- row heights
- label column width
- marker positions
- double-line positions
- sign positions
- arrow start/end points
- value label positions

This gives clean visuals without watermark while preserving the mathematical diagram.
