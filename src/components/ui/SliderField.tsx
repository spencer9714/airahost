export function SliderField({
  label,
  value,
  min,
  max,
  step = 1,
  displayValue,
  helperText,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  displayValue?: string;
  helperText?: string;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <label className="text-sm font-medium text-foreground">{label}</label>
        <span className="text-xs text-muted">{displayValue ?? String(value)}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-accent"
      />
      {helperText ? (
        <p className="mt-1 text-xs text-muted-foreground">{helperText}</p>
      ) : null}
    </div>
  );
}
