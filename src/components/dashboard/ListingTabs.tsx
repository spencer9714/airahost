interface ListingOption {
  id: string;
  name: string;
}

interface Props {
  listings: ListingOption[];
  selectedId: string;
  onChange: (id: string) => void;
  onMoreClick: () => void;
}

const MAX_VISIBLE = 3;

export function ListingTabs({
  listings,
  selectedId,
  onChange,
  onMoreClick,
}: Props) {
  if (listings.length <= 1) return null;

  const visible = listings.slice(0, MAX_VISIBLE);
  const hasMore = listings.length > MAX_VISIBLE;

  return (
    <div className="inline-flex gap-1 rounded-xl border border-border bg-gray-100/80 p-1">
      {visible.map((l) => (
        <button
          key={l.id}
          type="button"
          onClick={() => onChange(l.id)}
          className={`rounded-lg px-5 py-2.5 text-base font-semibold transition-all ${
            selectedId === l.id
              ? "bg-white text-foreground shadow-sm"
              : "text-foreground/60 hover:text-foreground"
          }`}
        >
          {l.name}
        </button>
      ))}
      {hasMore && (
        <button
          type="button"
          onClick={onMoreClick}
          className="rounded-lg px-5 py-2.5 text-base font-semibold text-foreground/60 transition-colors hover:text-foreground"
        >
          + More
        </button>
      )}
    </div>
  );
}
