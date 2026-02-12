/**
 * Python Pricing Service Adapter (Placeholder)
 *
 * TODO: When the Python pricing service is ready, this adapter will:
 *
 * 1. Accept the same PricingCoreInput interface
 * 2. Make an HTTP call to the Python service
 * 3. Map the Python response to PricingCoreOutput
 * 4. Handle retries, timeouts, and error mapping
 *
 * Integration boundary:
 * - The Python service will receive JSON matching PricingCoreInput
 * - It must return JSON matching PricingCoreOutput
 * - The contract is defined by Zod schemas in @/lib/schemas.ts
 *
 * Environment variables needed:
 * - PRICING_SERVICE_URL: Base URL of the Python service
 * - PRICING_SERVICE_API_KEY: API key for authentication
 *
 * To switch from mock to real:
 * 1. Implement this adapter
 * 2. Update the import in /api/reports/route.ts
 *    from: import { generatePricingReport } from "@/core/pricingCore"
 *    to:   import { generatePricingReport } from "@/core/pythonAdapter"
 */

export {};
