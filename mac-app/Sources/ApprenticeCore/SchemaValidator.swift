import Foundation

/// Validates JSON against the subset of JSON Schema that `schemas/*.json` use:
/// type, required, properties, additionalProperties (bool), items, enum, const, pattern, minimum, minItems, maxItems.
public enum SchemaValidator {
    public static func errors(in instance: Any, schema: [String: Any], path: String = "$") -> [String] {
        var foundErrors: [String] = []
        if let allowedTypes = schema["type"] {
            let typeNames = (allowedTypes as? [String]) ?? [allowedTypes as? String ?? ""]
            if !typeNames.contains(where: { matches(instance, typeName: $0) }) {
                return ["\(path): expected \(typeNames.joined(separator: "|")), got \(type(of: instance))"]
            }
        }
        if let allowedValues = schema["enum"] as? [Any], !allowedValues.contains(where: { isEqual($0, instance) }) {
            foundErrors.append("\(path): \(instance) not in enum")
        }
        if let constantValue = schema["const"], !isEqual(constantValue, instance) {
            foundErrors.append("\(path): expected const \(constantValue)")
        }
        if let pattern = schema["pattern"] as? String, let text = instance as? String,
           text.range(of: pattern, options: .regularExpression) == nil {
            foundErrors.append("\(path): \"\(text)\" doesn't match \(pattern)")
        }
        if let minimum = schema["minimum"] as? Double, let number = instance as? NSNumber, !isBoolean(instance),
           number.doubleValue < minimum {
            foundErrors.append("\(path): \(number) < \(minimum)")
        }
        if let object = instance as? [String: Any] {
            for requiredKey in schema["required"] as? [String] ?? [] where object[requiredKey] == nil {
                foundErrors.append("\(path): missing \(requiredKey)")
            }
            let propertySchemas = schema["properties"] as? [String: [String: Any]] ?? [:]
            for (key, value) in object {
                if let propertySchema = propertySchemas[key] {
                    foundErrors += errors(in: value, schema: propertySchema, path: "\(path).\(key)")
                } else if schema["additionalProperties"] as? Bool == false {
                    foundErrors.append("\(path): unexpected property \(key)")
                }
            }
        }
        if let array = instance as? [Any] {
            if let minItems = schema["minItems"] as? Int, array.count < minItems { foundErrors.append("\(path): fewer than \(minItems) items") }
            if let maxItems = schema["maxItems"] as? Int, array.count > maxItems { foundErrors.append("\(path): more than \(maxItems) items") }
            if let itemSchema = schema["items"] as? [String: Any] {
                for (index, item) in array.enumerated() { foundErrors += errors(in: item, schema: itemSchema, path: "\(path)[\(index)]") }
            }
        }
        return foundErrors
    }

    static func isBoolean(_ value: Any) -> Bool {
        guard let number = value as? NSNumber else { return false }
        return CFGetTypeID(number) == CFBooleanGetTypeID()
    }

    static func matches(_ value: Any, typeName: String) -> Bool {
        switch typeName {
        case "null": return value is NSNull
        case "boolean": return isBoolean(value)
        case "string": return value is String
        case "object": return value is [String: Any]
        case "array": return value is [Any]
        case "number": return value is NSNumber && !isBoolean(value)
        case "integer":
            guard let number = value as? NSNumber, !isBoolean(value) else { return false }
            return number.doubleValue.rounded() == number.doubleValue
        default: return false
        }
    }

    static func isEqual(_ left: Any, _ right: Any) -> Bool {
        (left as? NSObject)?.isEqual(right) ?? false
    }
}
