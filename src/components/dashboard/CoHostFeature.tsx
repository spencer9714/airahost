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

const AIRAHOST_COHOST_EMAIL = process.env.NEXT_PUBLIC_AIRAHOST_COHOST_EMAIL;

export function CoHostFeature({ listing }: CoHostFeatureProps) {
  const [showPopover, setShowPopover] = useState(false);
  const [copied, setCopied] = useState(false);
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

  const handleCopyEmail = () => {
    if (AIRAHOST_COHOST_EMAIL) {
      navigator.clipboard.writeText(AIRAHOST_COHOST_EMAIL);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (!listingId) {
    return (
      <div className="w-full">
        <button
          disabled
          title="Airbnb listing URL required"
          className="w-full rounded-xl bg-accent/10 py-2.5 text-sm font-semibold text-accent/40 cursor-not-allowed transition-colors"
        >
          Add Airahost as Co-host
        </button>
        <p className="mt-1.5 text-center text-xs text-foreground/40">
          Airbnb listing URL required
        </p>
      </div>
    );
  }

  const cohostInviteUrl = `https://www.airbnb.com/hosting/listings/editor/${listingId}/details/co-hosts/invite`;

  return (
    <div className="w-full space-y-2.5">
      <div className="flex items-center space-x-2">
        <a
          href={cohostInviteUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex-grow rounded-xl bg-accent py-2.5 text-center text-sm font-semibold text-white transition-colors hover:bg-accent/90"
        >
          Add Airahost as Co-host
        </a>
        <div className="relative" ref={popoverRef}>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setShowPopover(!showPopover);
            }}
            className="flex h-10 w-10 items-center justify-center rounded-xl bg-gray-100 text-foreground/50 transition-colors hover:bg-gray-200 hover:text-foreground/80"
            aria-label="More info"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="16" x2="12" y2="12"></line>
              <line x1="12" y1="8" x2="12.01" y2="8"></line>
            </svg>
          </button>

          {showPopover && (
            <div className="absolute bottom-full right-0 z-50 mb-2 w-72 rounded-xl border border-gray-200/80 bg-white p-4 shadow-lg" onClick={(e) => e.stopPropagation()}>
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

      {AIRAHOST_COHOST_EMAIL && (
        <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm">
          <span className="truncate text-xs font-medium text-gray-500">
            {AIRAHOST_COHOST_EMAIL}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              handleCopyEmail();
            }}
            className="flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-700"
            aria-label="Copy email"
            title="Copy email"
          >
            {copied ? (
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3 text-emerald-600">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
            )}
          </button>
        </div>
      )}
    </div>
  );
}