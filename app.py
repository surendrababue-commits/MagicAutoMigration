import xml.etree.ElementTree as ET
import json
import uuid
import base64
import streamlit as st

def convert_magic_to_custom_final(uploaded_file):
    tree = ET.parse(uploaded_file)
    root = tree.getroot()
    
    # 1. Dynamically identify EVERY Source Array Wrapper
    src_wrappers = {}
    for field in root.findall('.//SourceSchemas//Field'):
        xsi_type = field.attrib.get('{http://www.w3.org/2001/XMLSchema-instance}type', '')
        if xsi_type in ('JsonArrayComplexField', 'JsonArraySimpleField'):
            parent_id = field.find('Identifier').text
            for child in root.findall('.//SourceSchemas//Field'):
                child_id = child.find('Identifier').text
                # If it's a direct child of the array node, it's the wrapper (bypassing max_inst checks)
                if child_id and child_id.startswith(parent_id + '/') and child_id.count('/') == parent_id.count('/') + 1:
                    src_wrappers[child_id] = parent_id

    sorted_src_wrappers = sorted(src_wrappers.items(), key=lambda x: len(x[0]), reverse=True)
    
    # Pre-clean the known source array paths so nested wildcards match flawlessly
    clean_src_arrays = set()
    for wrapper, parent in sorted_src_wrappers:
        c_parent = parent
        for w, p in sorted_src_wrappers:
            c_parent = c_parent.replace(w + '/Value', p)
            c_parent = c_parent.replace(w + '/', p + '/')
        clean_src_arrays.add(c_parent)

    # 2. Identify Destination Arrays & Primitives
    dest_arrays = set()
    complex_dest_arrays = set()
    dest_fields = root.findall('.//DestinationSchemas//Field')
    
    for field in dest_fields:
        ident = field.find('Identifier')
        if ident is None or not ident.text:
            continue
            
        dest_id = ident.text
        max_inst = field.find('MaxInstance')
        
        # Strip ~Value and @ANY@ artifacts
        clean_id = dest_id.rsplit('/', 1)[0] if '~Value' in dest_id else dest_id
        clean_id = clean_id.replace('/@ANY@', '')

        if max_inst is not None and max_inst.text not in ('0', '1'):
            dest_arrays.add(clean_id)
            
        if '/' in clean_id:
            parent = clean_id.rsplit('/', 1)[0]
            complex_dest_arrays.add(parent)

    # 3. Parse connections
    connections = root.find('.//Connections')
    dest_to_source = {}
    if connections is not None:
        for conn in connections.findall('ConnectionData'):
            dest_to_source[conn.find('DestinationIdentifier').text] = conn.find('SourceIdentifier').text

    advanced_settings = {
        "delimiter": ",",
        "textQualifier": "\"",
        "includeHeader": True,
        "lineTerminator": "auto",
        "trimWhitespace": True,
        "skipEmptyRows": True,
        "nullValue": "",
        "quoteAllFields": False,
        "dateFormat": "yyyy-MM-dd",
        "numberFormat": "0.##",
        "alwaysCreateNodes": False,
        "createEmptyArrayNodes": False,
        "formatting": "None"
    }

    custom_mapping = {
        "sourceSettings": {
            "format": "json-schema",
            "title": "Source",
            "advanced": advanced_settings
        },
        "destinationSettings": {
            "format": "xsd",
            "title": "Destination",
            "advanced": advanced_settings,
            "outputTemplate": {} 
        },
        "mappings": []
    }

    def star_path(path, arrays):
        p = path
        for arr in sorted(list(arrays), key=len, reverse=True):
            p = p.replace(arr + '/', arr + '[*]/')
            if p == arr:
                p = arr + '[*]'
        return p

    def star_parent_path(path, arrays):
        p = path
        for arr in sorted(list(arrays), key=len, reverse=True):
            if p.startswith(arr + '/'):
                p = p.replace(arr + '/', arr + '[*]/')
        return p

    # Build Dynamic Output Template Base 
    column_order = []
    column_types = {}
    
    for field in dest_fields:
        ident = field.find('Identifier')
        if ident is None or not ident.text:
            continue
            
        dest_id = ident.text
        if '/' not in dest_id: 
            continue
            
        clean_id = dest_id.rsplit('/', 1)[0] if '~Value' in dest_id else dest_id
        clean_id = clean_id.replace('/@ANY@', '')
        
        # Only complex array parent folders are omitted. Primitive arrays are leaves and must be output.
        is_comp_base = (clean_id in complex_dest_arrays)
        if is_comp_base:
            continue
            
        final_dst = star_path(clean_id, dest_arrays)
        if final_dst not in column_order:
            column_order.append(final_dst)
            column_types[final_dst] = "string"

    custom_mapping["destinationSettings"]["outputTemplate"] = {
        "columnOrder": column_order,
        "columnTypes": column_types,
        "structure": {
            "type": "xsd",
            "headers": True,
            "recordPath": "Record[*]"
        }
    }

    mapped_destinations = set()

    # 4. Generate Mappings
    for field in dest_fields:
        dest_id_node = field.find('Identifier')
        if dest_id_node is None or not dest_id_node.text:
            continue
            
        dest_id = dest_id_node.text
        if dest_id == 'root' or ('/' not in dest_id):
            continue

        if dest_id in dest_to_source:
            src_id = dest_to_source[dest_id]
            if src_id == 'root':
                continue
                
            clean_dst = dest_id.rsplit('/', 1)[0] if '~Value' in dest_id else dest_id
            clean_dst = clean_dst.replace('/@ANY@', '')

            # Strip wrappers to restore exact object path matching
            clean_src = src_id
            for wrapper, parent in sorted_src_wrappers:
                if clean_src == wrapper:
                    clean_src = parent
                clean_src = clean_src.replace(wrapper + '/Value', parent)
                clean_src = clean_src.replace(wrapper + '/', parent + '/')

            is_complex_dest_arr = clean_dst in complex_dest_arrays

            # Complex base arrays trigger nested processing rules
            if is_complex_dest_arr and dest_id == clean_dst:
                final_src_auto = clean_src.replace('/', '.')
                final_dst_auto = clean_dst
                
                final_src_manual = star_parent_path(clean_src, clean_src_arrays).replace('/', '.')
                final_dst_manual = star_parent_path(clean_dst, dest_arrays)
                
                # Double mapping requirement for strictly nested arrays
                if final_dst_manual != final_dst_auto and final_dst_manual not in mapped_destinations:
                    custom_mapping["mappings"].append({
                        "id": str(uuid.uuid4()),
                        "sources": [{"path": final_src_manual, "type": "array"}],
                        "destination": {"path": final_dst_manual, "type": "array"},
                        "transform": {"type": "DIRECT", "params": {}},
                        "type": "manual"
                    })
                    mapped_destinations.add(final_dst_manual)
                
                # Base AUTO mapping requirement
                if final_dst_auto not in mapped_destinations:
                    custom_mapping["mappings"].append({
                        "id": str(uuid.uuid4()),
                        "sources": [{"path": final_src_auto, "type": "array"}],
                        "destination": {"path": final_dst_auto, "type": "array"},
                        "transform": {"type": "DIRECT"},
                        "type": "auto"
                    })
                    mapped_destinations.add(final_dst_auto)
                continue
            else:
                # Handles all terminal strings and primitive arrays inherently 
                final_src = star_path(clean_src, clean_src_arrays).replace('/', '.')
                final_dst = star_path(clean_dst, dest_arrays)
                
                if final_dst in mapped_destinations:
                    continue
                mapped_destinations.add(final_dst)

                custom_mapping["mappings"].append({
                    "id": str(uuid.uuid4()),
                    "sources": [{"path": final_src, "type": "string"}],
                    "destination": {"path": final_dst, "type": "string"},
                    "transform": {"type": "DIRECT", "params": {}},
                    "type": "manual"
                })
            
        else:
            # Handle Expression logic with base64 decoding protection
            expr_node = field.find('.//Expression/ExpressionVal')
            if expr_node is not None and expr_node.text:
                try:
                    decoded_expr = base64.b64decode(expr_node.text).decode('utf-8')
                    if decoded_expr.startswith("'") and decoded_expr.endswith("'"):
                        decoded_expr = decoded_expr[1:-1]
                except Exception:
                    decoded_expr = expr_node.text
                
                clean_dst = dest_id.rsplit('/', 1)[0] if '~Value' in dest_id else dest_id
                clean_dst = clean_dst.replace('/@ANY@', '')
                final_dst = star_path(clean_dst, dest_arrays)
                
                if final_dst in mapped_destinations:
                    continue
                mapped_destinations.add(final_dst)
                
                custom_mapping["mappings"].append({
                    "id": str(uuid.uuid4()),
                    "sources": [],
                    "destination": {"path": final_dst, "type": "string"},
                    "transform": {"type": "EXPRESSION"},
                    "expression": decoded_expr,
                    "type": "expression"
                })

    return json.dumps(custom_mapping, indent=2)


# UI Configuration
st.set_page_config(page_title="Magic xpi to JSON Converter")
st.title("Magic xpi to JSON Mapping Converter-3")
st.markdown("Upload your Magic xpi mapping XML to automatically generate the robust JSON schema.")

uploaded_file = st.file_uploader("Upload Magic xpi XML/TXT file", type=['xml', 'txt'])

if uploaded_file is not None:
    if st.button("Convert File"):
        with st.spinner("Analyzing schema and mapping nested parameters..."):
            try:
                json_output = convert_magic_to_custom_final(uploaded_file)
                st.success("Conversion complete! Schema matched to exact expected properties.")
                
                st.download_button(
                    label="Download Custom JSON Configuration",
                    data=json_output,
                    file_name="Converted_Mapping.json",
                    mime="application/json"
                )
            except Exception as e:
                st.error(f"An error occurred during conversion: {str(e)}")