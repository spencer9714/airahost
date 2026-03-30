export interface ListingInputAttributes {
  listingUrl?: string | null;
  listing_url?: string | null; // Legacy property
  [key: string]: unknown; // Allow other properties for forward compatibility
}

export interface Listing {
  id: string;
  name: string;
  input_attributes: ListingInputAttributes | Record<string, unknown> | null;
}