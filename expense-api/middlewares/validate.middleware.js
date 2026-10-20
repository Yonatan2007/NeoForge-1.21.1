// turns a zod error into a short, readable list: [{ field, message }]
function formatIssues(error) {
  return error.issues.map((issue) => ({
    field: issue.path.join(".") || undefined,
    message: issue.message,
  }));
}

export function validate(schema, source = "body") {
  return (req, res, next) => {
    const result = schema.safeParse(req[source] ?? {});
    if (!result.success) {
      return res.status(400).json({ message: "Validation failed", errors: formatIssues(result.error) });
    }
    // express 5 makes req.query a getter, so keep parsed values on req.validated
    req.validated = result.data;
    next();
  };
}
