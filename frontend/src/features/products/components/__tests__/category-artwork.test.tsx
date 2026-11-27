import { fireEvent, render } from "@testing-library/react";
import { expect, it } from "vitest";
import { CategoryArtwork } from "../category-artwork";

it("uses the category media URL and preserves a decorative fallback for broken images", () => {
  const { container, rerender } = render(<CategoryArtwork image="/media/editions.webp" />);
  const image = container.querySelector("img")!;
  expect(image.src).toContain("/media/editions.webp");
  expect(image).toHaveAttribute("loading", "lazy");
  fireEvent.error(image);
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("svg")).toBeInTheDocument();
  rerender(<CategoryArtwork image="/media/new.webp" />);
  expect(container.querySelector("img")?.src).toContain("/media/new.webp");
});
