import mongoose from "mongoose";

export function notFound(req, res) {
  res.status(404).json({ message: "Route not found" });
}

// eslint-disable-next-line no-unused-vars
export function errorHandler(err, req, res, next) {
  if (err.type === "entity.parse.failed") {
    return res.status(400).json({ message: "Invalid JSON body" });
  }
  if (err instanceof mongoose.Error.CastError) {
    return res.status(400).json({ message: `Invalid ${err.path}` });
  }
  if (err instanceof mongoose.Error.ValidationError) {
    const errors = Object.values(err.errors).map((e) => ({ field: e.path, message: e.message }));
    return res.status(400).json({ message: "Validation failed", errors });
  }
  if (err.code === 11000) {
    return res.status(409).json({ message: "Email already exists" });
  }

  // log the details on the server, never send the stack to the client
  console.error(err);
  res.status(500).json({ message: "Internal server error" });
}
