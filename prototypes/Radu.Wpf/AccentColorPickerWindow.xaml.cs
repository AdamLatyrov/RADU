using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using Brush = System.Windows.Media.Brush;
using Color = System.Windows.Media.Color;
using MouseEventArgs = System.Windows.Input.MouseEventArgs;
using Point = System.Windows.Point;

namespace Radu.Wpf;

public partial class AccentColorPickerWindow : Window
{
    private double _hue;
    private double _saturation;
    private double _value;
    private bool _updatingInput;
    private bool _inputIsValid = true;
    private bool _draggingPalette;
    private bool _draggingHue;

    public Color? SelectedColor { get; private set; }

    public AccentColorPickerWindow(Color initialColor)
    {
        InitializeComponent();
        SetColor(initialColor);
    }

    private void Header_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (e.ButtonState == MouseButtonState.Pressed)
            DragMove();
    }

    private void CloseButton_Click(object sender, RoutedEventArgs e) => Close();

    private void CancelButton_Click(object sender, RoutedEventArgs e) => DialogResult = false;

    private void ApplyButton_Click(object sender, RoutedEventArgs e)
    {
        if (!_inputIsValid || !TryParseHex(HexInput.Text, out var color))
        {
            SetInputValidity(false);
            return;
        }

        SelectedColor = color;
        DialogResult = true;
    }

    private void HexInput_TextChanged(object sender, System.Windows.Controls.TextChangedEventArgs e)
    {
        if (_updatingInput || HexInput is null)
            return;

        if (!TryParseHex(HexInput.Text, out var color))
        {
            SetInputValidity(false);
            return;
        }

        SetInputValidity(true);
        SetColor(color);
    }

    private void SaturationValuePalette_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        _draggingPalette = SaturationValuePalette.CaptureMouse();
        UpdateSaturationValue(e.GetPosition(SaturationValuePalette));
        e.Handled = true;
    }

    private void SaturationValuePalette_MouseMove(object sender, MouseEventArgs e)
    {
        if (_draggingPalette && e.LeftButton == MouseButtonState.Pressed)
            UpdateSaturationValue(e.GetPosition(SaturationValuePalette));
    }

    private void SaturationValuePalette_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        _draggingPalette = false;
        SaturationValuePalette.ReleaseMouseCapture();
        e.Handled = true;
    }

    private void HuePalette_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        _draggingHue = HuePalette.CaptureMouse();
        UpdateHue(e.GetPosition(HuePalette));
        e.Handled = true;
    }

    private void HuePalette_MouseMove(object sender, MouseEventArgs e)
    {
        if (_draggingHue && e.LeftButton == MouseButtonState.Pressed)
            UpdateHue(e.GetPosition(HuePalette));
    }

    private void HuePalette_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        _draggingHue = false;
        HuePalette.ReleaseMouseCapture();
        e.Handled = true;
    }

    private void Palette_SizeChanged(object sender, SizeChangedEventArgs e) => UpdateThumbPositions();

    private void UpdateSaturationValue(Point position)
    {
        _saturation = Math.Clamp(position.X / Math.Max(SaturationValuePalette.ActualWidth, 1), 0, 1);
        _value = 1 - Math.Clamp(position.Y / Math.Max(SaturationValuePalette.ActualHeight, 1), 0, 1);
        UpdateColorFromHsv();
    }

    private void UpdateHue(Point position)
    {
        _hue = Math.Clamp(position.Y / Math.Max(HuePalette.ActualHeight, 1), 0, 1) * 360;
        UpdateColorFromHsv();
    }

    private void SetColor(Color color)
    {
        (_hue, _saturation, _value) = ToHsv(color);
        UpdateColorFromHsv();
    }

    private void UpdateColorFromHsv()
    {
        var color = FromHsv(_hue, _saturation, _value);
        SelectedColor = color;
        HueColorLayer.Fill = new SolidColorBrush(FromHsv(_hue, 1, 1));
        ColorPreview.Background = new SolidColorBrush(color);
        CurrentHex.Text = $"#{color.R:X2}{color.G:X2}{color.B:X2}";

        _updatingInput = true;
        try
        {
            HexInput.Text = CurrentHex.Text;
        }
        finally
        {
            _updatingInput = false;
        }

        SetInputValidity(true);
        UpdateThumbPositions();
    }

    private void UpdateThumbPositions()
    {
        if (SaturationValuePalette is not null && SaturationValuePalette.ActualWidth > 0)
        {
            var paletteWidth = SaturationValuePalette.ActualWidth;
            var paletteHeight = SaturationValuePalette.ActualHeight;
            var left = _saturation * Math.Max(0, paletteWidth - SaturationThumbOuter.Width);
            var top = (1 - _value) * Math.Max(0, paletteHeight - SaturationThumbOuter.Height);
            Canvas.SetLeft(SaturationThumbOuter, left);
            Canvas.SetTop(SaturationThumbOuter, top);
            Canvas.SetLeft(SaturationThumbInner, left + (SaturationThumbOuter.Width - SaturationThumbInner.Width) / 2);
            Canvas.SetTop(SaturationThumbInner, top + (SaturationThumbOuter.Height - SaturationThumbInner.Height) / 2);
        }

        if (HuePalette is not null && HuePalette.ActualHeight > 0)
        {
            var top = _hue / 360 * Math.Max(0, HuePalette.ActualHeight - HueThumb.Height);
            Canvas.SetLeft(HueThumb, (HuePalette.ActualWidth - HueThumb.Width) / 2);
            Canvas.SetTop(HueThumb, top);
        }
    }

    private void SetInputValidity(bool isValid)
    {
        _inputIsValid = isValid;
        ValidationText.Foreground = (Brush)FindResource(isValid ? "MutedBrush" : "DangerBrush");
        ValidationText.Text = isValid
            ? "Можно ввести код HEX вручную"
            : "Введи 6 символов HEX, например #38BDF8";
    }

    private static bool TryParseHex(string value, out Color color)
    {
        var hex = value.Trim();
        if (hex.StartsWith('#'))
            hex = hex[1..];

        if (hex.Length == 6
            && byte.TryParse(hex[..2], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var red)
            && byte.TryParse(hex[2..4], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var green)
            && byte.TryParse(hex[4..], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var blue))
        {
            color = Color.FromRgb(red, green, blue);
            return true;
        }

        color = Colors.Transparent;
        return false;
    }

    private static (double Hue, double Saturation, double Value) ToHsv(Color color)
    {
        var red = color.R / 255d;
        var green = color.G / 255d;
        var blue = color.B / 255d;
        var maximum = Math.Max(red, Math.Max(green, blue));
        var minimum = Math.Min(red, Math.Min(green, blue));
        var delta = maximum - minimum;

        var hue = delta == 0
            ? 0
            : maximum == red
                ? 60 * (((green - blue) / delta) % 6)
                : maximum == green
                    ? 60 * ((blue - red) / delta + 2)
                    : 60 * ((red - green) / delta + 4);

        if (hue < 0)
            hue += 360;

        var saturation = maximum == 0 ? 0 : delta / maximum;
        return (hue, saturation, maximum);
    }

    private static Color FromHsv(double hue, double saturation, double value)
    {
        hue = ((hue % 360) + 360) % 360;
        var chroma = value * saturation;
        var secondary = chroma * (1 - Math.Abs((hue / 60 % 2) - 1));
        var offset = value - chroma;

        var (red, green, blue) = hue switch
        {
            < 60 => (chroma, secondary, 0d),
            < 120 => (secondary, chroma, 0d),
            < 180 => (0d, chroma, secondary),
            < 240 => (0d, secondary, chroma),
            < 300 => (secondary, 0d, chroma),
            _ => (chroma, 0d, secondary)
        };

        return Color.FromRgb(ToByte(red + offset), ToByte(green + offset), ToByte(blue + offset));
    }

    private static byte ToByte(double value) => (byte)Math.Round(Math.Clamp(value, 0, 1) * 255);
}
