import "@testing-library/jest-dom/vitest";
import { toHaveNoViolations } from "jest-axe";
import { expect } from "vitest";

/** axe-core assertions: `expect(await axe(container)).toHaveNoViolations()`. */
expect.extend(toHaveNoViolations);
