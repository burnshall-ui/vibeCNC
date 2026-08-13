# Contributing to Vibe CNC

Thank you for your interest in contributing to Vibe CNC! 🎉

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help others learn and grow
- Keep discussions professional

## How to Contribute

### 🐛 Reporting Bugs

1. **Check existing issues** - Search for similar problems first
2. **Create a detailed report** with:
   - Operating system and Python version
   - Steps to reproduce
   - Expected vs. actual behavior
   - Screenshots if applicable
   - Error messages or logs

### 💡 Suggesting Features

1. **Open an issue** with the `enhancement` label
2. **Describe the feature** clearly:
   - What problem does it solve?
   - How should it work?
   - Any examples or mockups?

### 🔧 Submitting Code

1. **Fork the repository**
2. **Create a feature branch**:
   ```bash
   git checkout -b feature/amazing-feature
   ```

3. **Make your changes**:
   - Follow the existing code style
   - Add comments for complex logic
   - Update documentation if needed

4. **Test your changes**:
   ```bash
   python vibe_cnc.py  # Manual testing
   ```

5. **Commit with a clear message**:
   ```bash
   git commit -m "feat: Add amazing feature"
   ```

   Use conventional commits:
   - `feat:` - New feature
   - `fix:` - Bug fix
   - `docs:` - Documentation only
   - `style:` - Code style (formatting, etc.)
   - `refactor:` - Code restructuring
   - `test:` - Adding tests
   - `chore:` - Maintenance tasks

6. **Push and create a Pull Request**:
   ```bash
   git push origin feature/amazing-feature
   ```

## Code Style

### Python
- **PEP 8** style guide
- **4 spaces** for indentation (no tabs)
- **Type hints** for function signatures (preferred)
- **Docstrings** for classes and complex functions
- **Meaningful variable names** (avoid single letters except in loops)

### Example
```python
def calculate_spindle_speed(diameter: float, cutting_speed: int) -> int:
    """
    Calculate spindle RPM from cutting speed and diameter.

    Args:
        diameter: Workpiece diameter in mm
        cutting_speed: Cutting speed in m/min

    Returns:
        Spindle speed in RPM
    """
    return int((cutting_speed * 1000) / (math.pi * diameter))
```

## Project Structure

```
vibe_cnc/
├── vibe_cnc.py              # Main application
├── vibe_cnc/
│   ├── claude_client.py     # AI integration
│   ├── gcode_plotter.py     # 2D visualization
│   ├── lint_engine.py       # Policy checker
│   └── ...
├── tools/                   # Tool database
├── programs/                # Example G-Code
└── config.yaml              # Configuration
```

## Testing

Currently, we rely on manual testing. When running tests:

1. **Load a sample program** from `programs/`
2. **Test simulation** - Check live drawing works
3. **Test AI features** - Verify analysis and generation
4. **Check UI responsiveness** - Resize, zoom, pan

## Documentation

When adding features:

1. **Update README.md** if it affects usage
2. **Add docstrings** to new functions
3. **Update config.yaml.example** if new settings added
4. **Include examples** for complex features

## Getting Help

- **Issues** - Ask questions by opening an issue
- **Discussions** - Use GitHub Discussions for general topics
- **Code Review** - Request feedback in your PR

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.

---

**Thank you for making Vibe CNC better!** 🚀
