interface SegmentedOption<T extends string> {
  label: string;
  value: T;
}

export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: SegmentedOption<T>[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex gap-1 rounded-xl border border-border p-0.5">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
            value === opt.value
              ? "bg-foreground text-white"
              : "text-muted hover:text-foreground"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
