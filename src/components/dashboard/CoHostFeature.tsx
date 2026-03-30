'use client';

import React, { useState, useRef, useEffect } from 'react';
import { extractAirbnbListingId } from '@/lib/airbnb-utils';
import { cohostBenefits } from './cohost';
import { Listing } from '@/lib/listing';

// As per product requirements, a normal web app cannot robustly auto-fill
// the co-host email on Airbnb's website due to cross-origin security policies.
// The recommended V2 path for a seamless auto-fill experience is to build
// a browser extension (e.g., a Chrome Extension) that can securely interact
// with the Airbnb page DOM after the user has logged in.

interface CoHostFeatureProps {
  listing: Listing;
}

export function CoHostFeature({ listing }: CoHostFeatureProps) {
  const [enabled, setEnabled] = useState(false);
  const [showPopover, setShowPopover] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Close popover when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        setShowPopover(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const listingUrl =
    (listing.input_attributes?.listingUrl as string | null | undefined) ||
    (listing.input_attributes?.listing_url as string | null | undefined);
  const listingId = extractAirbnbListingId(listingUrl);

  const cohostInviteUrl = listingId
    ? `https://www.airbnb.com/hosting/listings/editor/${listingId}/details/co-hosts/invite`
    : null;

  const isOn = enabled && !!listingId;

  return (
    <div
      className={`flex items-center justify-between px-5 py-3 transition-colors border-t ${
        isOn
          ? "border-gray-100/80"
          : "border-accent/10 bg-accent/5"
      }`}
      onClick={(e) => e.stopPropagation()}
    >
      {/* Left: label + info popover */}
      <div className="flex min-w-0 items-center gap-2">
        {!isOn && <span className="text-sm">🏠</span>}
        <span className={`text-sm font-semibold ${isOn ? "text-foreground/50" : "text-accent"}`}>
          Add Airahost as Co-host
        </span>
        {!listingId && (
          <span className="text-[11px] text-accent/50">Needs listing URL</span>
        )}

        {/* Info popover */}
        <div className="relative" ref={popoverRef}>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setShowPopover(!showPopover);
            }}
            className="flex items-center justify-center text-foreground/35 transition-colors hover:text-foreground/60"
            aria-label="More info"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="16" x2="12" y2="12"></line>
              <line x1="12" y1="8" x2="12.01" y2="8"></line>
            </svg>
          </button>
          {showPopover && (
            <div className="absolute bottom-full left-0 z-50 mb-2 w-72 rounded-xl border border-gray-200/80 bg-white p-4 shadow-lg" onClick={(e) => e.stopPropagation()}>
              <div className="space-y-2.5">
                <h4 className="font-semibold text-sm text-gray-900">{cohostBenefits.title}</h4>
                <p className="text-xs text-gray-500 leading-relaxed">{cohostBenefits.intro}</p>
                <ul className="ml-4 list-disc space-y-1 text-xs text-gray-700">
                  {cohostBenefits.benefits.map((benefit) => (
                    <li key={benefit}>{benefit}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Right: toggle */}
      <button
        type="button"
        role="switch"
        aria-checked={isOn}
        aria-label={isOn ? "Disable co-host" : "Enable co-host"}
        disabled={!listingId}
        onClick={() => {
          if (!enabled && cohostInviteUrl) {
            window.open(cohostInviteUrl, "_blank", "noopener,noreferrer");
          }
          setEnabled((v) => !v);
        }}
        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors focus:outline-none disabled:cursor-not-allowed disabled:opacity-60 ${
          isOn ? "bg-emerald-500" : "bg-accent"
        }`}
      >
        <span
          className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
            isOn ? "translate-x-4" : "translate-x-1"
          }`}
        />
      </button>
    </div>
  );
}